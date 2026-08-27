"""Backup domain objects."""

from __future__ import annotations

import dataclasses
import re

import yaesm.ty as ty
from yaesm.representation import Representation

_RepresentationT = ty.TypeVar("_RepresentationT", bound=Representation, covariant=True)

if ty.TYPE_CHECKING:
    from yaesm.pipeline import Pipeline
    from yaesm.retention import RetentionPolicyBase
    from yaesm.schedule import Schedule


class BackupError(Exception): ...


@dataclasses.dataclass(frozen=True)
class BackupOperation:
    """One scheduled or manually requested execution of a backup."""

    backup_name: str
    schedule_name: str
    created_at: ty.datetime

    @property
    def artifact_name(self) -> str:
        """Return the canonical name for the resulting artifact."""
        return self.created_at.strftime(
            f"yaesm-{self.backup_name}-{self.schedule_name}.%Y_%m_%d_%H:%M"
        )


@dataclasses.dataclass(frozen=True)
class BackupArtifact(ty.Generic[_RepresentationT]):
    """A successfully stored representation and the operation that created it."""

    operation: BackupOperation
    representation: _RepresentationT

    @property
    def name(self) -> str:
        """Return the canonical artifact name."""
        return self.operation.artifact_name


@dataclasses.dataclass(frozen=True)
class Backup:
    """A named backup definition used to create operations."""

    name: str
    pipeline: Pipeline
    schedules: tuple[Schedule, ...]
    retention_policies: tuple[RetentionPolicyBase, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][-_:@a-z0-9]*", self.name, re.IGNORECASE):
            raise ValueError(f"invalid backup name: {self.name!r}")
