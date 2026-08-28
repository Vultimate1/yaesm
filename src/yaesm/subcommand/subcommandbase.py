"""Base class for command-line subcommands."""

from __future__ import annotations

import abc
import argparse

import yaesm.ty as ty

if ty.TYPE_CHECKING:
    from yaesm.config import Config


class SubcommandBase(abc.ABC):
    """Contract for yaesm command-line subcommands."""

    hidden: ty.ClassVar[bool] = False
    config_required: ty.ClassVar[bool] = True

    @abc.abstractmethod
    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        """Run the subcommand and return its exit status."""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add this subcommand's arguments to a parser."""
        raise NotImplementedError

    @ty.final
    @classmethod
    def description(cls) -> str | None:
        """Return the class docstring used in command-line help."""
        return cls.__doc__

    @ty.final
    @classmethod
    def name(cls) -> str:
        """Derive the command name from the class name."""
        return cls.__name__.removesuffix("Subcommand").lower()
