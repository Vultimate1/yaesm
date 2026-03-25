"""tests/test_yaesm/test_subcommand/test_subcommandbase.py."""

import argparse

from yaesm.backup import Backup
from yaesm.subcommand.subcommandbase import SubcommandBase


class _StubSubcommand(SubcommandBase):
    """A stub subcommand for testing."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass


class _NoDocstringSubcommand(SubcommandBase):
    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass


def test_description_returns_class_docstring():
    assert _StubSubcommand.description() == "A stub subcommand for testing."


def test_description_returns_none_when_no_docstring():
    assert _NoDocstringSubcommand.description() is None
