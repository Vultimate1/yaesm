"""Tests for yaesm.names."""

import pytest

from yaesm.errors import YaesmValueError
from yaesm.names import ALL_TARGET_NAME, SETTINGS_NAME, name_valid, validate_name


@pytest.mark.parametrize(
    "name",
    ["home", "HOME", "5minute", "5-minute", "_temporary", "home_backup", "_", "a" * 64],
)
def test_name_valid_accepts_safe_names(name):
    assert name_valid(name)


def test_validate_name_returns_valid_name():
    assert validate_name("home-backup") == "home-backup"


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        1,
        "-home",
        "home.backup",
        "home@server",
        "home:local",
        "home/backups",
        "home backup",
        "home,root",
        "hôme",
        "İ",
        "ſ",
        "K",
        "a" * 65,
    ],
)
def test_name_valid_rejects_unsafe_names(name):
    assert not name_valid(name)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        (None, "name must be a string"),
        ("", "name must not be empty"),
        ("a" * 65, "name must not exceed 64 characters"),
        ("-home", "name must begin with an ASCII letter, digit, or underscore"),
        (
            "home backup",
            "name must contain only ASCII letters, digits, underscores, and hyphens",
        ),
    ],
)
def test_validate_name_reports_why_name_is_invalid(name, message):
    with pytest.raises(YaesmValueError, match=message):
        validate_name(name)


def test_all_target_name_is_outside_the_user_name_namespace():
    assert ALL_TARGET_NAME == "@all"
    assert not name_valid(ALL_TARGET_NAME)


def test_settings_name():
    assert SETTINGS_NAME == "settings"


@pytest.mark.parametrize("name", ["reserved", "RESERVED", "Reserved"])
def test_name_valid_rejects_reserved_names_case_insensitively(name):
    assert not name_valid(name, reserved=("reserved",))


def test_validate_name_reports_reserved_name():
    with pytest.raises(YaesmValueError, match="name is reserved"):
        validate_name("RESERVED", reserved=("reserved",))
