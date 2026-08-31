"""Validation of user-defined names."""

import re

import yaesm.ty as ty
from yaesm.errors import YaesmValueError

ALL_TARGET_NAME = "@all"
SETTINGS_NAME = "settings"


def validate_name(name: object, *, reserved: ty.Iterable[str] = ()) -> str:
    """Return a valid user-defined name or raise with the reason it is invalid."""
    if not isinstance(name, str):
        raise YaesmValueError("name must be a string")
    if not name:
        raise YaesmValueError("name must not be empty")
    if len(name) > 64:
        raise YaesmValueError("name must not exceed 64 characters")
    if any(name.casefold() == value.casefold() for value in reserved):
        raise YaesmValueError("name is reserved")
    if not re.fullmatch(r"[a-z0-9_]", name[0], re.ASCII | re.IGNORECASE):
        raise YaesmValueError("name must begin with an ASCII letter, digit, or underscore")
    if not re.fullmatch(r"[a-z0-9_-]+", name, re.ASCII | re.IGNORECASE):
        raise YaesmValueError(
            "name must contain only ASCII letters, digits, underscores, and hyphens"
        )
    return name


def name_valid(name: object, *, reserved: ty.Iterable[str] = ()) -> bool:
    """Return whether a name is safe, at most 64 characters, and not reserved."""
    try:
        validate_name(name, reserved=reserved)
    except YaesmValueError:
        return False
    return True
