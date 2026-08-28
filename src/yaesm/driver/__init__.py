"""Composable backup drivers."""

import importlib
import pkgutil


def load_drivers() -> None:
    """Import every driver module in this package."""
    for module in pkgutil.walk_packages(__path__, f"{__name__}."):
        importlib.import_module(module.name)
