"""Validation of user-defined names."""

import re

import yaesm.ty as ty


def name_valid(name: object, *, reserved: ty.Iterable[str] = ()) -> bool:
    """Return whether a name is safe and does not match a reserved name."""
    return (
        isinstance(name, str)
        and all(name.casefold() != value.casefold() for value in reserved)
        and bool(re.fullmatch(r"[a-z0-9_][a-z0-9_-]*", name, re.ASCII | re.IGNORECASE))
    )
