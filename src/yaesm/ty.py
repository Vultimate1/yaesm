"""yaesm.ty - Common type re-exports for annotation use.

Always import this module qualified:
    import yaesm.ty as ty
"""

from collections.abc import Callable, Generator, Iterator, Sequence
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path
from re import Match, Pattern
from subprocess import CompletedProcess
from typing import (
    Any,
    ClassVar,
    Final,
    Literal,
    NoReturn,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    final,
    overload,
)

__all__ = [
    "Any",
    "Callable",
    "ClassVar",
    "CompletedProcess",
    "datetime",
    "Final",
    "Generator",
    "Iterator",
    "Literal",
    "Logger",
    "Match",
    "NoReturn",
    "Path",
    "Pattern",
    "Protocol",
    "Sequence",
    "timedelta",
    "TypeAlias",
    "TypeVar",
    "cast",
    "final",
    "overload",
]
