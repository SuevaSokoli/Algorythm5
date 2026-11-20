"""Stage 8: duplicate detection (blocking + tiers), safety brake, and merging.

Within the job, records are compared only when they share a blocking key. Records with the
same first and last name from *different files* are judged on their details (email, phone,
birth date, address): matching details merge, conflicting details mean different people.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from itertools import combinations

from rapidfuzz import fuzz

from algorythm5.cleaning.models import ConsentStatus, PipelineSettings, Record, StageResult
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import fold

AUTO_MERGE_NAME_RATIO = 90
REVIEW_NAME_RATIO = 92
BRAKE_NAME_RATIO = 80
MIN_CLUSTER_SIZE = 2
MAX_CLUSTER_SIZE = 5
NAME_KEY_LAST_LETTERS = 3
LIST_SEPARATOR = "; "
MERGE_FIELDS_BY_LIST = {"email_other", "phone_other"}
SUPPORTING_FIELDS = ("city", "birth_date", "postal_code")
# Canonical output fields counted to choose the survivor and filled during merging.
MERGEABLE_FIELDS = (
    "customer_id", "salutation", "first_name", "last_name", "email", "email_other", "phone",
    "phone_extension", "phone_other", "company", "street", "postal_code", "city", "country",
    "birth_date", "signup_date", "notes",
)  # fmt: skip
# Plain-language names of the details compared for same-name records from different files.
DETAIL_LABELS = {
    "email": "email",
    "phone": "phone",
    "birth_date": "birth date",
    "address": "address",
}
# Tiers that merge automatically, and the rule code each one logs (first match wins).
MERGE_TIER_CODES = (("T1", "DUP-001"), ("T2", "DUP-002"), ("T4", "DUP-009"))
_E164 = re.compile(r"^\+\d{6,15}$")
_NOT_ALNUM = re.compile(r"[\W_]+")

AGREE, CONFLICT, WEAK = "agree", "conflict", "weak"


@dataclass(frozen=True, slots=True)
class PairMatch:
    tier: str  # "T1", "T2", "T3", "T4", "SHARED_PHONE", "DIFFERENT"
    first: int
    second: int


@dataclass(frozen=True, slots=True)
class DetailComparison:
    agree: tuple[str, ...]
    conflict: tuple[str, ...]


def _split(value: str) -> list[str]:
    return [v for v in value.split(LIST_SEPARATOR) if v]


def position(record: Record) -> int:
    """Upload order: later files (and later rows) have higher record numbers = newer data."""
    digits = record.record_id.lstrip("R")
    return int(digits) if digits.isdigit() else 0


def emails_of(record: Record) -> set[str]:
    """Primary + other valid emails (invalid ones in email_other are excluded)."""
    found = {record.get("email")} | {e for e in _split(record.get("email_other")) if "@" in e}
    return {e for e in found if e and not e.startswith("INVALID")}


def phones_of(record: Record) -> set[str]:
    found = {record.get("phone"), *_split(record.get("phone_other"))}
    return {p for p in found if _E164.match(p)}


def folded_name(record: Record) -> str:
    return fold(f"{record.get('first_name')} {record.get('last_name')}")


def name_ratio(a: Record, b: Record) -> float | None:
    """Similarity of full names, or None when either name is missing."""
    first, second = folded_name(a), folded_name(b)
    if not first or not second:
        return None
    return fuzz.token_sort_ratio(first, second)


def same_name_other_file(a: Record, b: Record) -> bool:
    """Same first and last name (ignoring case and accents), coming from two different files."""
    if not a.source_file or not b.source_file or a.source_file == b.source_file:
        return False
    names = [(fold(r.get("first_name")), fold(r.get("last_name"))) for r in (a, b)]
    return all(names[0]) and names[0] == names[1]


def _compact(value: str) -> str:
    return _NOT_ALNUM.sub("", fold(value))


def _compare_sets(first: set[str], second: set[str]) -> str | None:
    if not first or not second:
        return None
    return AGREE if first & second else CONFLICT


def _compare_values(first: str, second: str) -> str | None:
    if not first or not second:
        return None
    return AGREE if _compact(first) == _compact(second) else CONFLICT


def _compare_address(a: Record, b: Record) -> str | None:
    """Street, else postal code, decides; a matching city alone is too weak to merge on."""
    for field in ("street", "postal_code"):
        verdict = _compare_values(a.get(field), b.get(field))
        if verdict:
            return verdict
    verdict = _compare_values(a.get("city"), b.get("city"))
    return WEAK if verdict == AGREE else verdict


def compare_details(a: Record, b: Record) -> DetailComparison:
    verdicts = {
        "email": _compare_sets(emails_of(a), emails_of(b)),
        "phone": _compare_sets(phones_of(a), phones_of(b)),
        "birth_date": _compare_values(a.get("birth_date"), b.get("birth_date")),
        "address": _compare_address(a, b),
    }
    return DetailComparison(
        agree=tuple(f for f, v in verdicts.items() if v == AGREE),
        conflict=tuple(f for f, v in verdicts.items() if v == CONFLICT),
    )


def blocking_keys(record: Record) -> set[str]:
    keys = {f"e:{e}" for e in emails_of(record)} | {f"p:{p}" for p in phones_of(record)}
    first, last = fold(record.get("first_name")), fold(record.get("last_name"))
    if first and last:
        keys.add(f"n:{last[:NAME_KEY_LAST_LETTERS]}{first[0]}")
    return keys


def candidate_pairs(records: Sequence[Record]) -> list[tuple[int, int]]:
    blocks: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        for key in blocking_keys(record):
            blocks[key].append(index)
    pairs = {pair for members in blocks.values() for pair in combinations(members, 2)}
    return sorted(pairs)


def _supporting_match(a: Record, b: Record) -> bool:
    return any(a.get(f) and fold(a.get(f)) == fold(b.get(f)) for f in SUPPORTING_FIELDS)


def classify_pair(a: Record, b: Record) -> str | None:
    """Tier of a candidate pair (see CLAUDE.md §5 Duplicates), or None."""
    if same_name_other_file(a, b):
        details = compare_details(a, b)
        if details.conflict:
            return "DIFFERENT"
        return "T4" if details.agree else "T3"
    if emails_of(a) & emails_of(b):
        return "T1"
    ratio = name_ratio(a, b)
    if phones_of(a) & phones_of(b) and ratio is not None:
        return "T2" if ratio >= AUTO_MERGE_NAME_RATIO else "SHARED_PHONE"
    if ratio is not None and ratio >= REVIEW_NAME_RATIO and _supporting_match(a, b):
        return "T3"
    return None


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


@dataclass(frozen=True, slots=True)
class Brake:
    rule_code: str
    reason: str


def brake_reason(
    cluster: Sequence[Record], different: Iterable[frozenset[str]] = ()
) -> Brake | None:
    """Why a cluster must not be merged automatically, if any."""
    if len(cluster) > MAX_CLUSTER_SIZE:
        return Brake("DUP-004", f"more than {MAX_CLUSTER_SIZE} records")
    ids = {r.record_id for r in cluster}
    if any(pair <= ids for pair in different):
        return Brake("DUP-004", "it contains people with the same name but different details")
    primaries = {r.get("email") for r in cluster if r.get("email")}
    if len(primaries) > 1:
        return Brake("DUP-004", "different email addresses")
    for a, b in combinations(cluster, 2):
        ratio = name_ratio(a, b)
        if ratio is not None and ratio < BRAKE_NAME_RATIO:
            if emails_of(a) & emails_of(b):
                return Brake("DUP-007", "names are too different")
            return Brake("DUP-004", "names are too different")
    return None


def _filled_count(record: Record) -> int:
    return sum(1 for f in MERGEABLE_FIELDS if record.get(f))


def choose_survivor(cluster: Sequence[Record]) -> Record:
    """Most filled fields wins; ties go to the lowest source row."""
    return min(cluster, key=lambda r: (-_filled_count(r), position(r), r.source_row))


def _merge_consent(cluster: Iterable[Record]) -> str:
    statuses = {r.get("consent_status") for r in cluster}
    for status in (ConsentStatus.OPTED_OUT, ConsentStatus.SUBSCRIBED):
        if status.value in statuses:
            return status.value
    return ConsentStatus.UNKNOWN.value


def _append_unique(current: str, *additions: str) -> str:
    items = _split(current)
    for addition in additions:
        items.extend(a for a in _split(addition) if a not in items)
    return LIST_SEPARATOR.join(items)


def resolve_conflict(mine: str, theirs: str, theirs_is_newer: bool) -> tuple[str, str]:
    """(value to keep, why): the more complete value, otherwise the newer one."""
    compact_mine, compact_theirs = _compact(mine), _compact(theirs)
    if compact_mine and compact_mine in compact_theirs:
        return theirs, "more complete"
    if compact_theirs and compact_theirs in compact_mine:
        return mine, "more complete"
    return (theirs, "newer") if theirs_is_newer else (mine, "newer")


class _Merger:
    """Folds the other members of a cluster into the survivor, logging every decision."""

    def __init__(self, survivor: Record, rule_code: str, log: StageLog) -> None:
        self.survivor = survivor
        self.rule_code = rule_code
        self.log = log
        self.values = dict(survivor.values)
        self.extras = dict(survivor.extras)
        # Which record (by position) supplied each current value; decides "newer".
        self.origin: dict[str, int] = {}

    def _newer(self, key: str, other: Record) -> bool:
        return position(other) > self.origin.get(key, position(self.survivor))

    def _resolve(self, target: dict[str, str], key: str, other: Record, theirs: str) -> None:
        mine = target.get(key, "")
        kept, why = resolve_conflict(mine, theirs, self._newer(key, other))
        dropped = mine if kept == theirs else theirs
        detail = f"kept '{kept}' ({why}), set aside '{dropped}'"
        self.log.issue(self.survivor, key, "DUP-006", original_value=dropped, detail=detail)
        if kept == theirs:
            target[key] = theirs
            self.origin[key] = position(other)
            self.log.change(self.survivor, key, mine, theirs, self.rule_code)

    def field(self, other: Record, field: str) -> None:
        mine, theirs = self.values.get(field, ""), other.get(field)
        if not theirs or fold(mine) == fold(theirs):
            return
        if field in MERGE_FIELDS_BY_LIST:
            self.values[field] = _append_unique(mine, theirs)
        elif not mine:
            self.values[field] = theirs
            self.origin[field] = position(other)
        elif field in ("email", "phone"):
            target = f"{field}_other"
            before = self.values.get(target, "")
            self.values[target] = _append_unique(before, theirs)
            self.log.change(self.survivor, target, before, self.values[target], self.rule_code)
            return
        elif field == "signup_date" and theirs < mine:
            self.values[field] = theirs  # the earliest sign-up is the real one
        elif field == "signup_date":
            return
        else:
            self._resolve(self.values, field, other, theirs)
            return
        self.log.change(self.survivor, field, mine, self.values[field], self.rule_code)

    def passthrough(self, other: Record, header: str, value: str) -> None:
        mine = self.extras.get(header, "")
        if not value or fold(mine) == fold(value):
            return
        if not mine:
            self.extras[header] = value
            self.origin[header] = position(other)
            self.log.change(self.survivor, header, "", value, self.rule_code)
        else:
            self._resolve(self.extras, header, other, value)


def merge_cluster(
    cluster: Sequence[Record], rule_code: str, log: StageLog
) -> tuple[Record, list[Record]]:
    """Merge a cluster into its survivor; returns (survivor, absorbed members)."""
    survivor = choose_survivor(cluster)
    others = sorted((r for r in cluster if r is not survivor), key=position)
    merger = _Merger(survivor, rule_code, log)
    for other in others:
        for field in MERGEABLE_FIELDS:
            merger.field(other, field)
        for header, value in other.extras.items():
            merger.passthrough(other, header, value)
    values = merger.values
    consent = _merge_consent(cluster)
    log.change(survivor, "consent_status", values.get("consent_status", ""), consent, rule_code)
    values["consent_status"] = consent
    origins = [origin for r in sorted(cluster, key=position) for origin in r.origins()]
    merged = replace(
        survivor.with_values(values, merger.extras),
        source_rows=tuple(row for _, row in origins),
        source_files=tuple(file for file, _ in origins),
    )
    related = [r.record_id for r in others]
    log.issue(merged, None, rule_code, related=related)
    absorbed = []
    for other in others:
        log.change(other, "merged_into", "", survivor.record_id, rule_code)
        absorbed.append(replace(other, merged_into=survivor.record_id))
    return merged, absorbed


def _merge_code(tiers: set[str]) -> str:
    return next((code for tier, code in MERGE_TIER_CODES if tier in tiers), "DUP-001")


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    del settings
    log = StageLog()
    items = list(records)
    union = _UnionFind(len(items))
    tiers_by_root: dict[int, set[str]] = defaultdict(set)
    review_pairs: list[tuple[int, int]] = []
    different_pairs: list[tuple[int, int]] = []
    for a, b in candidate_pairs(items):
        tier = classify_pair(items[a], items[b])
        if tier in ("T1", "T2", "T4"):
            union.union(a, b)
            tiers_by_root[min(a, b)].add(tier)
        elif tier == "T3":
            review_pairs.append((a, b))
        elif tier == "DIFFERENT":
            different_pairs.append((a, b))
        elif tier == "SHARED_PHONE":
            log.issue(items[a], "phone", "DUP-005", related=[items[b].record_id])
            log.issue(items[b], "phone", "DUP-005", related=[items[a].record_id])
    different = [frozenset((items[a].record_id, items[b].record_id)) for a, b in different_pairs]
    position_of = {record.record_id: index for index, record in enumerate(items)}
    clusters: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        clusters[union.find(index)].append(index)
    tier_of_cluster: dict[int, set[str]] = defaultdict(set)
    for root, tiers in tiers_by_root.items():
        tier_of_cluster[union.find(root)] |= tiers
    for root, members in clusters.items():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        cluster = [items[i] for i in members]
        brake = brake_reason(cluster, different)
        if brake:
            ids = [r.record_id for r in cluster]
            for record in cluster:
                others = [i for i in ids if i != record.record_id]
                log.issue(record, None, brake.rule_code, detail=brake.reason, related=others)
            continue
        survivor, absorbed = merge_cluster(cluster, _merge_code(tier_of_cluster[root]), log)
        for member in (survivor, *absorbed):
            items[position_of[member.record_id]] = member
    _flag_pairs(items, review_pairs, log)
    _flag_pairs(items, different_pairs, log, different=True)
    return log.result(items)


def _flag_pairs(
    items: Sequence[Record],
    pairs: Iterable[tuple[int, int]],
    log: StageLog,
    *,
    different: bool = False,
) -> None:
    """DUP-003 (possible duplicate) or DUP-008 (same name, different people) on both records.

    Points at survivors when a member was merged into another record.
    """
    by_id = {r.record_id: r for r in items}

    def resolve(record: Record) -> Record:
        return by_id[record.merged_into] if record.merged_into else record

    seen: set[tuple[str, str]] = set()
    for a, b in pairs:
        first, second = resolve(items[a]), resolve(items[b])
        key = (min(first.record_id, second.record_id), max(first.record_id, second.record_id))
        if first.record_id == second.record_id or key in seen:
            continue
        seen.add(key)
        if not different:
            log.issue(first, None, "DUP-003", related=[second.record_id])
            log.issue(second, None, "DUP-003", related=[first.record_id])
            continue
        conflict = compare_details(items[a], items[b]).conflict
        detail = "different " + ", ".join(DETAIL_LABELS[f] for f in conflict)
        for record, other in ((first, second), (second, first)):
            name = f"{record.get('first_name')} {record.get('last_name')}".strip()
            log.issue(
                record, None, "DUP-008", original_value=name, detail=detail,
                related=[other.record_id],
            )  # fmt: skip
