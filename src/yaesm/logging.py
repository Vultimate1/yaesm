"""Uniform standard-library logging configuration for yaesm.

Call `configure()` once at startup and use `logging.getLogger(__name__)`
elsewhere.
"""

import contextvars
import logging
import logging.handlers
from pathlib import Path
from uuid import UUID

import yaesm.ty as ty

request_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "yaesm_request_id", default=None
)
current_backup: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "yaesm_current_backup", default=None
)
_record_factory = logging.getLogRecordFactory()


def _yaesm_record_factory(*args: ty.Any, **kwargs: ty.Any) -> logging.LogRecord:
    """Create a log record whose logger name is under the yaesm namespace."""
    record = _record_factory(*args, **kwargs)
    if not record.name.startswith("yaesm."):
        record.name = f"yaesm.{record.name}"
    return record


def format_duration(seconds: float) -> str:
    """Format elapsed seconds for user-facing log messages."""
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{hours}h"] if hours else []) + ([f"{minutes}m"] if minutes else [])
    return " ".join((*parts, f"{seconds}s"))


class RequestFilter(logging.Filter):
    """Keep log records emitted for one control request."""

    def __init__(self, expected: UUID) -> None:
        super().__init__()
        self.expected = expected

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether the current request matches this filter."""
        return request_id.get() == self.expected


def configure(
    level: int | str = logging.INFO,
    *,
    stderr: bool = False,
    message_only_stderr: bool = False,
    logfile: Path | str | None = None,
    syslog_address: str | None = None,
) -> None:
    """Configure the selected logging destinations, defaulting to stderr."""
    if not (stderr or logfile or syslog_address):
        stderr = True
    handlers: list[logging.Handler] = []
    if stderr:
        handler = logging.StreamHandler()
        if message_only_stderr:
            handler.setFormatter(logging.Formatter("yaesm: %(message)s"))
        handlers.append(handler)
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    if syslog_address:
        handlers.append(logging.handlers.SysLogHandler(address=syslog_address))
    logging.setLogRecordFactory(_yaesm_record_factory)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
