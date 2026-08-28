"""Tests for yaesm.subcommand.subcommandbase."""

import argparse
import inspect

from yaesm.config import Config
from yaesm.subcommand.subcommandbase import SubcommandBase


class StubSubcommand(SubcommandBase):
    """A stub subcommand."""

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        del config, arguments
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        del parser


class NoDocstringSubcommand(SubcommandBase):
    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        del config, arguments
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        del parser


class MissingMainSubcommand(SubcommandBase):
    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        del parser


class MissingArgumentsSubcommand(SubcommandBase):
    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        del config, arguments
        return 0


def test_subcommand_defaults():
    assert not StubSubcommand.hidden
    assert StubSubcommand.config_required


def test_name_is_derived_from_class_name():
    assert StubSubcommand.name() == "stub"


def test_description_uses_class_docstring():
    assert StubSubcommand.description() == "A stub subcommand."
    assert NoDocstringSubcommand.description() is None


def test_subcommand_contract_is_required():
    assert inspect.isabstract(MissingMainSubcommand)
    assert inspect.isabstract(MissingArgumentsSubcommand)
