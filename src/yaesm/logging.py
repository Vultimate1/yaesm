"""Uniform standard-library logging configuration for yaesm.

Call `configure()` once at startup and use `logging.getLogger(__name__)`
elsewhere.
"""

import logging


def configure(level: int | str = logging.INFO) -> None:
    """Configure logging to standard error with yaesm's format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
