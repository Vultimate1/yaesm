"""Tests for yaesm.subcommand.subcommandbase."""

import argparse
import inspect

import pytest

from yaesm.config import Config
from yaesm.names import ALL_TARGET_NAME
from yaesm.subcommand.subcommandbase import (
    SubcommandBase,
    TargetSelection,
    TargetSelectionMode,
)


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


class NoTargetSelectionSubcommand(StubSubcommand):
    pass


class NoTargetsSubcommand(StubSubcommand):
    target_selection = TargetSelectionMode.NONE


class RequiredTargetsSubcommand(StubSubcommand):
    target_selection = TargetSelectionMode.REQUIRED


class DefaultAllTargetsSubcommand(StubSubcommand):
    target_selection = TargetSelectionMode.DEFAULT_ALL


class TargetThenQuerySubcommand(StubSubcommand):
    target_selection = TargetSelectionMode.REQUIRED

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("query", nargs="*")


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


def test_target_selection_contract_is_required():
    with pytest.raises(TypeError, match="must declare target_selection"):
        NoTargetSelectionSubcommand.configure_argparser(argparse.ArgumentParser())


def test_subcommand_without_targets_only_adds_its_own_arguments():
    parser = argparse.ArgumentParser()

    NoTargetsSubcommand.configure_argparser(parser)

    assert parser.parse_args([]) == argparse.Namespace()
    with pytest.raises(SystemExit):
        parser.parse_args(["home"])


def test_required_target_selection_accepts_names_and_all():
    parser = argparse.ArgumentParser()
    RequiredTargetsSubcommand.configure_argparser(parser)

    named = parser.parse_args(["home,,root, home"]).targets
    all_targets = parser.parse_args([ALL_TARGET_NAME]).targets

    assert named == TargetSelection(names=("home", "root"))
    assert not named.all
    assert all_targets == TargetSelection((ALL_TARGET_NAME,))
    assert all_targets.all


def test_target_selection_cannot_be_empty():
    with pytest.raises(ValueError, match="target selection cannot be empty"):
        TargetSelection(())


def test_all_target_cannot_be_combined_with_other_targets():
    with pytest.raises(ValueError, match="cannot be combined with other targets"):
        TargetSelection((ALL_TARGET_NAME, "home"))


def test_required_target_selection_rejects_omission_and_empty_names():
    parser = argparse.ArgumentParser()
    RequiredTargetsSubcommand.configure_argparser(parser)

    for values in ((), (","), (f"{ALL_TARGET_NAME},home",)):
        with pytest.raises(SystemExit):
            parser.parse_args(values)


def test_default_all_target_selection_uses_all_when_omitted():
    parser = argparse.ArgumentParser()
    DefaultAllTargetsSubcommand.configure_argparser(parser)

    assert parser.parse_args([]).targets == TargetSelection((ALL_TARGET_NAME,))
    assert parser.parse_args(["home"]).targets == TargetSelection(names=("home",))


def test_all_target_occupies_the_first_positional_argument():
    parser = argparse.ArgumentParser()
    TargetThenQuerySubcommand.configure_argparser(parser)

    arguments = parser.parse_args([ALL_TARGET_NAME, "newest"])

    assert arguments.targets == TargetSelection((ALL_TARGET_NAME,))
    assert arguments.query == ["newest"]
