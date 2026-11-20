"""One error envelope for every failure: {"error": {"code", "message"}}. Never leaks internals."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from algorythm5.services import AppError

logger = logging.getLogger(__name__)

HTTP_MESSAGES = {
    404: "Not found.",
    405: "This action is not allowed here.",
    413: "The file is too large.",
}
GENERIC_ERROR = "Something went wrong on our side. Please try again."


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        return await _unexpected_error(request, exc)
    return error_response(exc.http_status, exc.code, exc.message)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    return error_response(422, "VALIDATION", "Some of the information sent was not valid.")


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return await _unexpected_error(request, exc)
    message = HTTP_MESSAGES.get(exc.status_code, "The request could not be completed.")
    return error_response(exc.status_code, f"HTTP_{exc.status_code}", message)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "INTERNAL", GENERIC_ERROR)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
