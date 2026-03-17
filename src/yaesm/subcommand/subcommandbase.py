import abc
import argparse

import yaesm.ty as ty
from yaesm.backup import Backup


class SubcommandBase(abc.ABC):
    """Abstract base class for subcommand classes such as FindSubcommand and
    RunSubcommand. An actual subcommand class inherits from SubcommandBase.

    Implementers of a Subcommand class must implement the
    `add_argparser_arguments()` and `main()` abstract methods.
    """

    @abc.abstractmethod
    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        """The function that actually executes the subcommand.

        Is passed a list of yaesm.backup.Backup object (`backups`) from the user
        configuration file, and is also passed parsed arguments that were parsed
        from this Subcommands arg parser (see `add_argparser_arguments()`).

        Returns program exit status.
        """
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add this subcommand's CLI arguments to `parser`. Mutates `parser`."""
        raise NotImplementedError

    @ty.final
    @classmethod
    def name(cls) -> str:
        """Automatically derive subcommand name from class name.

        Converts 'FindSubcommand' -> 'find', 'RunSubcommand' -> 'run', etc.
        """
        class_name = cls.__name__
        subcommand_name = class_name[:-10]  # Remove 'Subcommand' suffix
        return subcommand_name.lower()
