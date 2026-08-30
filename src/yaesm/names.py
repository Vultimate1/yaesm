"""Validation of user-defined names."""

import re

import yaesm.ty as ty

ALL_TARGET_NAME = "@all"
SETTINGS_NAME = "settings"


def name_valid(name: object, *, reserved: ty.Iterable[str] = ()) -> bool:
    """Return whether a name is safe, at most 64 characters, and not reserved."""
    return (
        isinstance(name, str)
        and len(name) <= 64
        and all(name.casefold() != value.casefold() for value in reserved)
        and bool(re.fullmatch(r"[a-z0-9_][a-z0-9_-]*", name, re.ASCII | re.IGNORECASE))
    )
