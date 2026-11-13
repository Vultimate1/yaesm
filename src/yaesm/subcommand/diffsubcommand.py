"""The diff subcommand."""

import argparse
import contextlib
import dataclasses
import difflib
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
                    differences = (
                        compare_file(before, after, arguments.file)
                        if arguments.file is not None
                        else (f"{status:<8}{path}" for status, path in compare_trees(before, after))
                    )
                    _print_differences(differences, heading=heading, end=end)
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
            "--file",
            type=Path,
            metavar="PATH",
            help="show changes to one file relative to both roots",
        )
        parser.add_argument(
            "-0", dest="null", action="store_true", help="separate results with NUL bytes"
        )


def _print_differences(differences: ty.Iterable[str], *, heading: str | None, end: str) -> None:
    """Print relative changes, adding an artifact heading only for nonempty results."""
    for record in differences:
        if heading is not None:
            print(f"{heading}:", end=end)
            heading = None
        print(record, end=end)


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


def compare_file(before: PathTree, after: PathTree, path: Path) -> ty.Iterator[str]:
    """Yield a unified text diff or a status record for one relative file."""
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise DiffError("--file must name a file relative to the backup root, without '..'")
    if before.ssh is not None or after.ssh is not None:
        raise DiffError("file comparison currently requires local trees")
    if any(
        excluded == path or excluded in path.parents
        for excluded in (*before.excluded_paths, *after.excluded_paths)
    ):
        return
    old, new = _read_file(before.path, path), _read_file(after.path, path)
    if old is None and new is None:
        raise DiffError(f"file is missing from both trees: {path}")
    if old == new:
        return
    status = "added" if old is None else "removed" if new is None else "changed"
    record = f"{status:<8}{path}"
    if isinstance(old, str) or isinstance(new, str) or not (old or new):
        yield record
        return
    left, right = old or b"", new or b""
    try:
        old_text, new_text = left.decode("utf-8"), right.decode("utf-8")
    except UnicodeDecodeError:
        yield f"{record} (binary)"
        return
    if "\0" in old_text or "\0" in new_text:
        yield f"{record} (binary)"
        return
    for line in difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{path}" if old is not None else "/dev/null",
        tofile=f"b/{path}" if new is not None else "/dev/null",
    ):
        yield line.removesuffix("\n")
        if not line.endswith("\n"):
            yield r"\ No newline at end of file"


def _read_file(root: Path, relative: Path) -> bytes | str | None:
    """Return file bytes, a symlink's target string, or None when the path is missing."""
    if not stat.S_ISDIR(root.stat().st_mode):
        raise DiffError(f"comparison root is not a directory: {root}")
    path = root
    for part in relative.parts:
        path /= part
        try:
            mode = stat.S_IFMT(path.lstat().st_mode)
        except FileNotFoundError:
            return None
        if path != root / relative and mode != stat.S_IFDIR:
            raise DiffError(f"file path traverses a symlink or non-directory: {path}")
    if mode == stat.S_IFLNK:
        return os.readlink(path)
    if mode == stat.S_IFREG:
        with open(path, "rb") as stream:
            return stream.read()
    raise DiffError(f"--file requires a regular file or symlink: {path}")


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
