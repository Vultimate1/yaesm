"""Tests for yaesm.names."""

import pytest

from yaesm.names import name_valid


@pytest.mark.parametrize(
    "name",
    ["home", "HOME", "5minute", "5-minute", "_temporary", "home_backup", "_"],
)
def test_name_valid_accepts_safe_names(name):
    assert name_valid(name)


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
    ],
)
def test_name_valid_rejects_unsafe_names(name):
    assert not name_valid(name)


@pytest.mark.parametrize("name", ["reserved", "RESERVED", "Reserved"])
def test_name_valid_rejects_reserved_names_case_insensitively(name):
    assert not name_valid(name, reserved=("reserved",))
