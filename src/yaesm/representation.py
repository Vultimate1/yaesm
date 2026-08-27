"""Data representations exchanged between driver capabilities."""

import dataclasses
import enum

import yaesm.ty as ty
from yaesm.ssh import SSHTarget


class DataProperty(enum.Enum):
    """Properties preserved while data moves through a pipeline."""

    SNAPSHOT = "snapshot"
    COMPRESSED = "compressed"
    ENCRYPTED = "encrypted"


class Representation:
    """Base type for data produced and consumed by driver capabilities."""


class ReadableTree(Representation):
    """A directory tree that can be read file by file."""


@dataclasses.dataclass(frozen=True)
class PathTree(ReadableTree):
    """A directory tree available at a local or remote path."""

    path: ty.Path
    target: SSHTarget | None = None


class BlockDevice(Representation):
    """A readable block device."""


class ByteStream(Representation):
    """A stream of bytes."""


@dataclasses.dataclass(frozen=True)
class CommandStream(ByteStream):
    """A byte stream produced by a pipeline of external commands."""

    commands: tuple[tuple[str, ...], ...] = ()


class UncompressedStream(CommandStream):
    """A byte stream that has not been compressed."""


class CompressedStream(CommandStream):
    """A compressed byte stream."""


class EncryptedStream(CommandStream):
    """An encrypted byte stream."""
