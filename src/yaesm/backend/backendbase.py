"""src/yaesm/backend/backendbase.py."""

from __future__ import annotations

import abc
import dataclasses
import importlib
import logging
from functools import cache
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm import config
from yaesm.timeframe import Timeframe

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """The result of one backup precondition check."""

    description: str
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.errors


class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as `RsyncBackend` and `BtrfsBackend`.

    Backend implementations are expected to implement `check()`, `create()`,
    `collect()`, and `delete()`.
    """

    def __init__(self, extra_opts: list[str] | None = None) -> None:
        self.extra_opts = extra_opts

    @ty.final
    def do_backup(self, backup: bckp.Backup, timeframe: Timeframe) -> None:
        """Perform a `backup` for a given `timeframe`.

        Note that this function also cleans up old backups.
        """
        backup_basename = bckp.backup_basename_now(backup, timeframe)
        backups = self.collect(backup, timeframes=[timeframe])
        if any(artifact.name == backup_basename for artifact in backups):
            logger.error(f"backup already exists: {backup_basename}")
            raise bckp.BackupError(f"backup already exists: {backup_basename}")
        backups.append(self.create(backup, timeframe, backup_basename))
        backups.sort(key=lambda artifact: artifact.created_at, reverse=True)
        to_delete = backups[timeframe.keep :]
        if to_delete:
            self.delete(backup, to_delete)

    @classmethod
    @ty.final
    def name(cls) -> str:
        """Automatically derive backend name from class name.

        Converts `BtrfsBackend` -> 'btrfs', `RsyncBackend` -> 'rsync', etc.
        """
        class_name = cls.__name__
        backend_name = class_name[:-7]  # Remove 'Backend' suffix
        return backend_name.lower()

    @staticmethod
    def config_settings() -> set[str]:
        """Returns the set of valid configuration setting names for this backend."""
        return set()

    @staticmethod
    def config_schema() -> vlp.Schema:
        """Returns a voluptuous schema for this backends specific configuration.

        See the yaesm.config module for more information.
        """
        return config.Schema.schema_empty()

    @staticmethod
    def config_schema_extra() -> vlp.Schema:
        """Returns a voluptuous schema to be applied to the configuration data circumstantially.

        More complicated or IO-driven validation should happen in this schema.
        See the yaesm.config module for more information.
        """
        return config.Schema.schema_empty()

    def format_locator(self, artifact: bckp.BackupArtifact) -> str:
        """Return the user-facing locator for a backup artifact."""
        return artifact.locator

    @abc.abstractmethod
    def check(self, backup: bckp.Backup) -> list[CheckResult]:
        """Check that preconditions for `backup` are met."""

    @abc.abstractmethod
    def create(self, backup: bckp.Backup, timeframe: Timeframe, name: str) -> bckp.BackupArtifact:
        """Create and return a stored backup artifact."""

    @abc.abstractmethod
    def collect(
        self, backup: bckp.Backup, timeframes: list[Timeframe] | None = None
    ) -> list[bckp.BackupArtifact]:
        """Collect stored backup artifacts from newest to oldest."""

    @abc.abstractmethod
    def delete(self, backup: bckp.Backup, artifacts: list[bckp.BackupArtifact]) -> None:
        """Delete stored backup artifacts."""

    @staticmethod
    @ty.final
    @cache
    def backend_classes() -> list[type[BackendBase]]:
        """Returns a list of all the backend classes.

        This is made possible with the use of a naming convention for backend
        classes. Backend modules named "yaesm.backend.${BACKEND_NAME_LOWERCASE}backend".
        Within each backend module there is a class named "${BACKEND_NAME_CAPITALIZED}Backend".
        """
        backend_dir = Path(__file__).parent
        backend_files = backend_dir.glob("*backend.py")
        backend_classes = []
        for f in backend_files:
            class_name = f.stem.replace("backend", "").capitalize() + "Backend"
            module_name = f"yaesm.backend.{class_name.lower()}"
            module = importlib.import_module(module_name)
            backend_class = getattr(module, class_name)
            backend_classes.append(backend_class)
        return backend_classes
