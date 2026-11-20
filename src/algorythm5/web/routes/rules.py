from __future__ import annotations

from fastapi import APIRouter

from algorythm5.cleaning import RULES
from algorythm5.web.schemas import RuleOut

router = APIRouter(prefix="/api", tags=["rules"])


@router.get("/rules")
def list_rules() -> list[RuleOut]:
    return [
        RuleOut(code=rule.code, severity=rule.severity.value, explanation=rule.explanation)
        for rule in RULES.values()
    ]
