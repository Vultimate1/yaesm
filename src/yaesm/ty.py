"""Common type re-exports for annotation use.

Always import this module qualified:
    import yaesm.ty as ty
"""

from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path
from re import Match, Pattern
from subprocess import CompletedProcess
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Generic,
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
    "Generic",
    "Generator",
    "Iterable",
    "Iterator",
    "Literal",
    "Logger",
    "Mapping",
    "Match",
    "NoReturn",
    "Path",
    "Pattern",
    "Protocol",
    "Sequence",
    "timedelta",
    "TYPE_CHECKING",
    "TypeAlias",
    "TypeVar",
    "cast",
    "final",
    "overload",
]
