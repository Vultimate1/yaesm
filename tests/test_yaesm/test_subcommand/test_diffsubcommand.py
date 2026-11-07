"""Tests for yaesm.subcommand.diffsubcommand."""

import argparse

import pytest

from yaesm.subcommand.diffsubcommand import DiffSubcommand
from yaesm.subcommand.findsubcommand import FindSubcommand


@pytest.mark.parametrize(
    "values",
    [
        ("home",),
        ("@all", "newest"),
        ("home,root", "between", "2026-01-01", "2026-02-01"),
        ("home", "newest", "--query", "oldest", "-q", "closest", "12:30"),
        ("home", "-q", "oldest"),
        ("home,,root, home", "--schedules", "daily,,daily", "--schedule", "weekly", "-s", "manual"),
    ],
)
def test_diff_accepts_the_same_selection_arguments_as_find(values):
    find_parser = argparse.ArgumentParser()
    diff_parser = argparse.ArgumentParser()
    FindSubcommand.configure_argparser(find_parser)
    DiffSubcommand.configure_argparser(diff_parser)

    expected = find_parser.parse_args(values)
    actual = diff_parser.parse_args(values)

    assert actual.targets == expected.targets
    assert actual.query == expected.query
    assert actual.additional_queries == expected.additional_queries
    assert actual.schedules == expected.schedules


@pytest.mark.parametrize(
    "values",
    [(), ("@all,home",), ("home", "--query"), ("home", "--schedule")],
)
def test_diff_rejects_the_same_invalid_selection_arguments_as_find(values):
    for subcommand in (FindSubcommand, DiffSubcommand):
        parser = argparse.ArgumentParser()
        subcommand.configure_argparser(parser)

        with pytest.raises(SystemExit) as caught:
            parser.parse_args(values)

        assert caught.value.code == 2
