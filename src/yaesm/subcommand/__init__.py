"""Command-line subcommands."""

import importlib
import pkgutil


def load_subcommands() -> None:
    """Import every subcommand module in this package."""
    for module in pkgutil.walk_packages(__path__, f"{__name__}."):
        importlib.import_module(module.name)
