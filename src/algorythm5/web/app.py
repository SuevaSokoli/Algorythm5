"""FastAPI app factory: routes, static UI, security headers, startup tasks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from algorythm5.config import Settings, get_settings
from algorythm5.db.connection import Database
from algorythm5.services.jobs import Clock, JobService, utc_now
from algorythm5.services.retention import purge_expired
from algorythm5.storage.files import FileStorage
from algorythm5.web.errors import error_response, register_error_handlers
from algorythm5.web.routes import health, jobs, rules

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"
PURGE_INTERVAL_SECONDS = 3_600
# Multipart framing around the file; requests larger than limit + this are refused early.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )


async def _purge_periodically(database: Database, storage: FileStorage, clock: Clock) -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(purge_expired, database, storage, clock())
        except Exception:
            logger.exception("retention purge failed")


def create_app(settings: Settings | None = None, clock: Clock = utc_now) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging()
    database = Database(settings.database_path)
    storage = FileStorage(settings.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.migrate()
        purge_expired(database, storage, clock())
        task = asyncio.create_task(_purge_periodically(database, storage, clock))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Algorythm5",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,  # Swagger/ReDoc load assets from a CDN; the app makes no outside calls
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.job_service = JobService(database, storage, settings, clock)
    register_error_handlers(app)

    upload_limit = settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    @app.middleware("http")
    async def guard_and_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > upload_limit:
            response: Response = error_response(
                413, "ING-902", "The file is too large (size, rows or columns)."
            )
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    for router in (health.router, rules.router, jobs.router):
        app.include_router(router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    return app


app = create_app()
