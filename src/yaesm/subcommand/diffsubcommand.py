"""The diff subcommand."""

import argparse
import contextlib
import dataclasses
import enum
import os
import stat
from functools import partial
from pathlib import Path

import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupSource
from yaesm.config import Config
from yaesm.driver.tardriver import TarTree
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.pipeline import detransform, exclude_artifact_roots
from yaesm.query import ArtifactQueryError, add_query_arguments, query_artifacts
from yaesm.representation import PathTree
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


class DiffError(YaesmValueError):
    """Raised when backups cannot be compared."""


class DiffMode(enum.Enum):
    """Select what a stored backup is compared against."""

    SOURCE = "source"
    DIRECTORY = "directory"
    BACKUP = "backup"


class DiffSubcommand(SubcommandBase):
    """List differences between stored backups and their source, a directory, or another backup."""

    target_selection = TargetSelectionMode.REQUIRED

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        try:
            selections = [
                (backup, artifact)
                for backup, artifacts in query_artifacts(config, arguments)
                for artifact in artifacts
            ]
            references = {}
            if arguments.against_backup is not None:
                reference_arguments = argparse.Namespace(**vars(arguments))
                reference_arguments.query = arguments.against_backup
                reference_arguments.additional_queries = []
                references = {
                    backup.name: artifacts
                    for backup, artifacts in query_artifacts(config, reference_arguments)
                }
        except ArtifactQueryError as error:
            raise DiffError("cannot compare backups") from error

        mode = DiffMode.SOURCE if arguments.against_dir is None else DiffMode.DIRECTORY
        if arguments.against_backup is not None:
            mode = DiffMode.BACKUP
        match mode:
            case DiffMode.SOURCE:
                prepare_comparison = source_comparison
            case DiffMode.DIRECTORY:
                prepare_comparison = partial(
                    directory_comparison, reference=PathTree(arguments.against_dir)
                )
            case DiffMode.BACKUP:
                prepare_comparison = partial(backup_comparison, references=references)
            case _:
                raise DiffError(f"unsupported diff mode: {mode!r}")

        multiple = len(selections) > 1
        end = "\0" if arguments.null else "\n"
        for backup, artifact in selections:
            try:
                with prepare_comparison(backup, artifact) as (before, after):
                    heading = backup.destination.format_locator(artifact) if multiple else None
                    _print_differences(compare_trees(before, after), heading=heading, end=end)
            except OSError as error:
                raise DiffError(f"cannot compare backup {backup.name!r}: {error}") from error
            except YaesmError as error:
                raise DiffError(f"cannot compare backup {backup.name!r}") from error
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add shared backup selection arguments."""
        add_query_arguments(parser)
        reference = parser.add_mutually_exclusive_group()
        reference.add_argument(
            "--against-dir",
            type=Path,
            metavar="PATH",
            help="compare with this local directory instead of the configured source",
        )
        reference.add_argument(
            "--against-backup",
            nargs="+",
            metavar="QUERY",
            help="compare with exactly one matching backup per target",
        )
        parser.add_argument(
            "-0", dest="null", action="store_true", help="separate results with NUL bytes"
        )


def _print_differences(
    differences: ty.Iterable[tuple[str, ty.Path]], *, heading: str | None, end: str
) -> None:
    """Print relative changes, adding an artifact heading only for nonempty results."""
    for status, path in differences:
        if heading is not None:
            print(f"{heading}:", end=end)
            heading = None
        print(f"{status:<8}{path}", end=end)


@contextlib.contextmanager
def source_comparison(
    backup: Backup, artifact: BackupArtifact
) -> ty.Generator[tuple[PathTree, PathTree], None, None]:
    """Prepare the backup and live source, keeping extracted files until the caller exits."""
    if isinstance(backup.source, BackupSource):
        raise DiffError("comparison requires a directory source, not a backup source")
    source = backup.source.cap_source()
    if not isinstance(source, PathTree) or source.ssh is not None:
        raise DiffError("comparison currently requires a local directory source")
    source = exclude_artifact_roots(source, backup.destination, backup.name)
    with directory_comparison(backup, artifact, source) as trees:
        yield trees


@contextlib.contextmanager
def directory_comparison(
    backup: Backup, artifact: BackupArtifact, reference: PathTree
) -> ty.Generator[tuple[PathTree, PathTree], None, None]:
    """Prepare a backup and reference directory, cleaning up any temporary extraction."""
    if not stat.S_ISDIR(reference.path.stat().st_mode):
        raise DiffError(f"comparison path is not a directory: {reference.path}")
    with detransform(artifact.representation, backup.transforms) as tree:
        if not isinstance(tree, PathTree):
            raise DiffError("stored backup does not provide a directory tree")
        if isinstance(tree, TarTree) and tree.ssh is None:
            # Do not report our own temporary extraction as added content.
            with contextlib.suppress(ValueError):
                temporary = tree.temporary_directory.resolve().relative_to(reference.path.resolve())
                reference = dataclasses.replace(
                    reference, excluded_paths=(*reference.excluded_paths, temporary)
                )
        yield tree, reference


@contextlib.contextmanager
def backup_comparison(
    backup: Backup, artifact: BackupArtifact, references: dict[str, tuple[BackupArtifact, ...]]
) -> ty.Generator[tuple[PathTree, PathTree], None, None]:
    """Prepare both stored backups, keeping extracted trees alive during comparison."""
    matches = references[backup.name]
    if len(matches) != 1:
        raise DiffError(
            f"--against-backup requires exactly one match for {backup.name!r}; found {len(matches)}"
        )
    with detransform(matches[0].representation, backup.transforms) as reference:
        if not isinstance(reference, PathTree) or reference.ssh is not None:
            raise DiffError("reference backup does not provide a local directory tree")
        with directory_comparison(backup, artifact, reference) as trees:
            yield trees


def compare_trees(before: PathTree, after: PathTree) -> ty.Iterator[tuple[str, Path]]:
    """Yield relative content/type changes, including empty directories, without following links."""
    if before.ssh is not None or after.ssh is not None:
        raise DiffError("directory comparison currently requires local trees")
    excluded = frozenset(Path(path) for path in (*before.excluded_paths, *after.excluded_paths))
    if Path(".") not in excluded:
        yield from _compare_directory(before.path, after.path, Path(), excluded)


def _entries(path: Path | None) -> dict[str, os.DirEntry[str]]:
    if path is None:
        return {}
    with os.scandir(path) as entries:
        return {entry.name: entry for entry in entries}


def _compare_directory(
    before: Path | None,
    after: Path | None,
    relative: Path,
    excluded: frozenset[Path],
) -> ty.Iterator[tuple[str, Path]]:
    old_entries, new_entries = _entries(before), _entries(after)
    for name in sorted(old_entries.keys() | new_entries.keys()):
        path = relative / name
        if path in excluded:
            continue
        old, new = old_entries.get(name), new_entries.get(name)
        if old is None:
            yield "added", path
        elif new is None:
            yield "removed", path
        elif not _same_entry(old, new):
            yield "changed", path
        if (old is None or old.is_dir(follow_symlinks=False)) and (
            new is None or new.is_dir(follow_symlinks=False)
        ):
            yield from _compare_directory(
                Path(old.path) if old else None, Path(new.path) if new else None, path, excluded
            )


def _same_entry(before: os.DirEntry[str], after: os.DirEntry[str]) -> bool:
    old, new = before.stat(follow_symlinks=False), after.stat(follow_symlinks=False)
    if stat.S_IFMT(old.st_mode) != stat.S_IFMT(new.st_mode):
        return False
    if stat.S_ISDIR(old.st_mode):
        return True
    if stat.S_ISLNK(old.st_mode):
        return os.readlink(before.path) == os.readlink(after.path)
    if not stat.S_ISREG(old.st_mode):
        return old.st_rdev == new.st_rdev
    if old.st_size != new.st_size:
        return False
    if os.path.samestat(old, new):
        return True
    with open(before.path, "rb") as left, open(after.path, "rb") as right:
        while True:
            chunk = left.read(1024 * 1024)
            if chunk != right.read(1024 * 1024):
                return False
            if not chunk:
                return True
