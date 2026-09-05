"""Base class for command-line subcommands."""

from __future__ import annotations

import abc
import argparse
import dataclasses
import enum

import yaesm.ty as ty
from yaesm.names import ALL_TARGET_NAME

if ty.TYPE_CHECKING:
    from yaesm.config import Config


class TargetSelectionMode(enum.Enum):
    """How a subcommand selects backup targets."""

    NONE = "none"
    REQUIRED = "required"
    DEFAULT_ALL = "default-all"


@dataclasses.dataclass(frozen=True)
class TargetSelection:
    """A normalized, nonempty selection of target names."""

    names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("target selection cannot be empty")
        if ALL_TARGET_NAME in self.names and self.names != (ALL_TARGET_NAME,):
            raise ValueError(f"{ALL_TARGET_NAME} cannot be combined with other targets")

    @property
    def all(self) -> bool:
        """Return whether all configured backups are selected."""
        return self.names == (ALL_TARGET_NAME,)


def _parse_target_selection(value: str) -> TargetSelection:
    names = tuple(dict.fromkeys(filter(None, map(str.strip, value.split(",")))))
    if not names:
        raise argparse.ArgumentTypeError("target selection must contain at least one name")
    try:
        return TargetSelection(names)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


class SubcommandBase(abc.ABC):
    """Contract for yaesm command-line subcommands."""

    target_selection: ty.ClassVar[TargetSelectionMode]
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
    def configure_argparser(cls, parser: argparse.ArgumentParser) -> None:
        """Add common target arguments and this subcommand's arguments to a parser."""
        mode = cls.__dict__.get("target_selection")
        if not isinstance(mode, TargetSelectionMode):
            raise TypeError(f"{cls.__name__} must declare target_selection")

        if mode is not TargetSelectionMode.NONE:
            parser.add_argument(
                "targets",
                nargs=None if mode is TargetSelectionMode.REQUIRED else "?",
                default=(
                    None
                    if mode is TargetSelectionMode.REQUIRED
                    else TargetSelection((ALL_TARGET_NAME,))
                ),
                metavar="TARGET[,TARGET...]",
                type=_parse_target_selection,
                help=f"names of backup targets ({ALL_TARGET_NAME} selects all)",
            )

        cls.add_argparser_arguments(parser)

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
