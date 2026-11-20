from __future__ import annotations

from fastapi import APIRouter, Request

from algorythm5.cleaning import RULESET_VERSION
from algorythm5.db.connection import Database
from algorythm5.web.schemas import Health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> Health:
    database: Database = request.app.state.database
    with database.transaction() as connection:
        connection.execute("SELECT 1")
    return Health(status="ok", ruleset_version=RULESET_VERSION)
