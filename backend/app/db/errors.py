"""Keep unhandled database diagnostics inside the HTTP boundary."""
import logging

from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse


logger = logging.getLogger("reawote.database")


class DatabaseResponseInterrupted(RuntimeError):
    """A response already started; the connection must terminate without details."""


class DatabaseErrorBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except (SQLAlchemyError, ExceptionGroup) as error:
            if isinstance(error, ExceptionGroup) and error.subgroup(SQLAlchemyError) is None:
                raise
            # Never attach the exception, its traceback, SQL/parameters, or request
            # context. Server DETAIL can contain values even with hide_parameters.
            logger.error("database_request_failed", extra={"response_started": started})
            if started:
                raise DatabaseResponseInterrupted("Database response interrupted.") from None
            await JSONResponse(
                {"detail": {"code": "DATABASE_UNAVAILABLE"}}, status_code=503,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )(scope, receive, send)
