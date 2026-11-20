"""Stage 8: duplicate detection (blocking + tiers), safety brake, and merging."""

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
_E164 = re.compile(r"^\+\d{6,15}$")


@dataclass(frozen=True, slots=True)
class PairMatch:
    tier: str  # "T1", "T2", "T3", "SHARED_PHONE"
    first: int
    second: int


def _split(value: str) -> list[str]:
    return [v for v in value.split(LIST_SEPARATOR) if v]


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


def brake_reason(cluster: Sequence[Record]) -> str | None:
    """Why a cluster must not be merged automatically, if any."""
    if len(cluster) > MAX_CLUSTER_SIZE:
        return f"more than {MAX_CLUSTER_SIZE} records"
    primaries = {r.get("email") for r in cluster if r.get("email")}
    if len(primaries) > 1:
        return "different email addresses"
    for a, b in combinations(cluster, 2):
        ratio = name_ratio(a, b)
        if ratio is not None and ratio < BRAKE_NAME_RATIO:
            return "names are too different"
    return None


def _filled_count(record: Record) -> int:
    return sum(1 for f in MERGEABLE_FIELDS if record.get(f))


def choose_survivor(cluster: Sequence[Record]) -> Record:
    """Most filled fields wins; ties go to the lowest source row."""
    return min(cluster, key=lambda r: (-_filled_count(r), r.source_row))


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


def merge_cluster(
    cluster: Sequence[Record], rule_code: str, log: StageLog
) -> tuple[Record, list[Record]]:
    """Merge a cluster into its survivor; returns (survivor, absorbed members)."""
    survivor = choose_survivor(cluster)
    others = sorted((r for r in cluster if r is not survivor), key=lambda r: r.source_row)
    values = dict(survivor.values)
    extras = dict(survivor.extras)
    for other in others:
        for field in MERGEABLE_FIELDS:
            _merge_field(survivor, other, field, values, log, rule_code)
        for header, value in other.extras.items():
            if value and not extras.get(header):
                extras[header] = value
                log.change(survivor, header, "", value, rule_code)
            elif value and fold(value) != fold(extras.get(header, "")):
                detail = f"kept '{extras[header]}', dropped value from {other.record_id}"
                log.issue(survivor, header, "DUP-006", original_value=value, detail=detail)
    consent = _merge_consent(cluster)
    log.change(survivor, "consent_status", values.get("consent_status", ""), consent, rule_code)
    values["consent_status"] = consent
    source_rows = tuple(sorted(row for r in cluster for row in r.source_rows))
    merged = replace(survivor.with_values(values, extras), source_rows=source_rows)
    related = [r.record_id for r in others]
    log.issue(merged, None, rule_code, related=related)
    absorbed = []
    for other in others:
        log.change(other, "merged_into", "", survivor.record_id, rule_code)
        absorbed.append(replace(other, merged_into=survivor.record_id))
    return merged, absorbed


def _merge_field(
    survivor: Record,
    other: Record,
    field: str,
    values: dict[str, str],
    log: StageLog,
    rule_code: str,
) -> None:
    mine, theirs = values.get(field, ""), other.get(field)
    if not theirs or fold(mine) == fold(theirs):
        return
    if field in MERGE_FIELDS_BY_LIST:
        values[field] = _append_unique(mine, theirs)
    elif not mine:
        values[field] = theirs
    elif field in ("email", "phone"):
        target = f"{field}_other"
        before = values.get(target, "")
        values[target] = _append_unique(before, theirs)
        log.change(survivor, target, before, values[target], rule_code)
        return
    elif field == "signup_date" and theirs < mine:
        values[field] = theirs
    else:
        detail = f"kept '{mine}', dropped value from {other.record_id}"
        log.issue(survivor, field, "DUP-006", original_value=theirs, detail=detail)
        return
    log.change(survivor, field, mine, values[field], rule_code)


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    del settings
    log = StageLog()
    items = list(records)
    union = _UnionFind(len(items))
    tiers_by_root: dict[int, set[str]] = defaultdict(set)
    review_pairs: list[tuple[int, int]] = []
    for a, b in candidate_pairs(items):
        tier = classify_pair(items[a], items[b])
        if tier in ("T1", "T2"):
            union.union(a, b)
            tiers_by_root[min(a, b)].add(tier)
        elif tier == "T3":
            review_pairs.append((a, b))
        elif tier == "SHARED_PHONE":
            log.issue(items[a], "phone", "DUP-005", related=[items[b].record_id])
            log.issue(items[b], "phone", "DUP-005", related=[items[a].record_id])
    position = {record.record_id: index for index, record in enumerate(items)}
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
        reason = brake_reason(cluster)
        if reason:
            ids = [r.record_id for r in cluster]
            for record in cluster:
                others = [i for i in ids if i != record.record_id]
                log.issue(record, None, "DUP-004", detail=reason, related=others)
            continue
        code = "DUP-001" if "T1" in tier_of_cluster[root] else "DUP-002"
        survivor, absorbed = merge_cluster(cluster, code, log)
        for member in (survivor, *absorbed):
            items[position[member.record_id]] = member
    _flag_review_pairs(items, review_pairs, log)
    return log.result(items)


def _flag_review_pairs(
    items: Sequence[Record], pairs: Iterable[tuple[int, int]], log: StageLog
) -> None:
    """DUP-003 for probable duplicates, pointing at survivors when a member was merged."""
    by_id = {r.record_id: r for r in items}

    def resolve(record: Record) -> Record:
        return by_id[record.merged_into] if record.merged_into else record

    seen: set[tuple[str, str]] = set()
    for a, b in pairs:
        first, second = resolve(items[a]), resolve(items[b])
        key = tuple(sorted((first.record_id, second.record_id)))
        if first.record_id == second.record_id or key in seen:
            continue
        seen.add((key[0], key[1]))
        log.issue(first, None, "DUP-003", related=[second.record_id])
        log.issue(second, None, "DUP-003", related=[first.record_id])
