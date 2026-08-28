"""Uniform standard-library logging configuration for yaesm.

Call `configure()` once at startup and use `logging.getLogger(__name__)`
elsewhere.
"""

import contextvars
import logging
from uuid import UUID

request_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "yaesm_request_id", default=None
)


class RequestFilter(logging.Filter):
    """Keep log records emitted for one control request."""

    def __init__(self, expected: UUID) -> None:
        super().__init__()
        self.expected = expected

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether the current request matches this filter."""
        return request_id.get() == self.expected


def configure(level: int | str = logging.INFO) -> None:
    """Configure logging to standard error with yaesm's format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
