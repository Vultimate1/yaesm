"""Tests for yaesm.subcommand.diffsubcommand."""

import argparse
import dataclasses
import os
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from yaesm.backup import Backup, BackupArtifact, BackupOperation, BackupSource
from yaesm.config import BackupGroup, Config
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.filedriver import FileDriver
from yaesm.driver.rsyncdriver import RsyncDriver, RsyncTree
from yaesm.driver.tardriver import TarDriver
from yaesm.pipeline import Pipeline
from yaesm.query import ArtifactQueryError
from yaesm.representation import PathTree, Representation
from yaesm.schedule import CronSchedule, Schedule
from yaesm.ssh import SSHTarget
from yaesm.subcommand.diffsubcommand import DiffError, DiffSubcommand, compare_trees
from yaesm.subcommand.findsubcommand import FindSubcommand


@pytest.mark.parametrize(
    "values",
    [
        ("home",),
        ("@all", "newest"),
        ("home,root", "between", "2026-01-01", "2026-02-01"),
        ("home", "newest", "--query", "oldest", "-q", "closest", "12:30"),
        ("home", "-q", "oldest"),
        ("home", "newest", "-0"),
        ("home,,root, home", "--schedules", "daily,,daily", "--schedule", "weekly", "-s", "manual"),
    ],
)
def test_diff_accepts_the_same_selection_arguments_as_find(values):
    find_parser = argparse.ArgumentParser()
    diff_parser = argparse.ArgumentParser()
    FindSubcommand.configure_argparser(find_parser)
    DiffSubcommand.configure_argparser(diff_parser)

    expected = find_parser.parse_args(values)
    actual = diff_parser.parse_args(values)

    assert actual.against_dir is None
    assert actual.targets == expected.targets
    assert actual.query == expected.query
    assert actual.additional_queries == expected.additional_queries
    assert actual.schedules == expected.schedules
    assert actual.null == expected.null


@pytest.mark.parametrize(
    "values",
    [(), ("@all,home",), ("home", "--query"), ("home", "--schedule")],
)
def test_diff_rejects_the_same_invalid_selection_arguments_as_find(values):
    for subcommand in (FindSubcommand, DiffSubcommand):
        parser = argparse.ArgumentParser()
        subcommand.configure_argparser(parser)

        with pytest.raises(SystemExit) as caught:
            parser.parse_args(values)

        assert caught.value.code == 2


def arguments(*values):
    parser = argparse.ArgumentParser()
    DiffSubcommand.configure_argparser(parser)
    return parser.parse_args(values)


@pytest.mark.parametrize(
    "values",
    [
        ("home", "newest", "--against-dir", "reference tree"),
        ("--against-dir", "reference tree", "home", "newest"),
        ("home", "newest", "--against-dir=reference tree"),
    ],
)
def test_diff_parses_reference_directory_without_changing_selection(values):
    parsed = arguments(*values)
    assert parsed.against_dir == Path("reference tree")
    assert parsed.targets.names == ("home",)
    assert parsed.query == ["newest"]


def test_diff_requires_reference_directory_argument():
    with pytest.raises(SystemExit) as caught:
        arguments("home", "--against-dir")
    assert caught.value.code == 2


@pytest.fixture
def backup(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    storage = tmp_path / "backups"
    storage.mkdir()
    artifacts = []
    for hour, schedule in ((10, "daily"), (12, "hourly")):
        operation = BackupOperation("home", schedule, datetime(2026, 8, 20, hour))
        tree = storage / operation.artifact_name
        tree.mkdir()
        artifacts.append(BackupArtifact(operation, RsyncTree(tree)))
    destination = RsyncDriver(storage)
    monkeypatch.setattr(destination, "cap_list", mock.Mock(return_value=tuple(artifacts)))
    return Backup(
        "home",
        DirectoryDriver(source),
        destination,
        schedules=(Schedule("nightly", CronSchedule("0 1 * * *"), previous_names=("daily",)),),
        previous_names=("old-home",),
    )


def config_for(backup):
    # Return artifacts once, even though the fixture also declares a backup alias.
    artifacts = backup.destination.cap_list.return_value
    backup.destination.cap_list.side_effect = lambda name: artifacts if name == "home" else ()
    return Config({}, {"home": backup}, {"group": BackupGroup("group", ("home",))})


def test_diff_compares_newest_with_configured_source(backup, capsys, monkeypatch, tmp_path):
    config = config_for(backup)
    newest = backup.artifacts()[0].representation.path
    (newest / "changed").write_text("old")
    (newest / "removed").write_text("old")
    (backup.source.location / "changed").write_text("new")
    (backup.source.location / "added").write_text("new")
    monkeypatch.chdir(tmp_path)

    assert DiffSubcommand().main(config, arguments("home", "newest")) == 0

    assert capsys.readouterr().out == "added   added\nchanged changed\nremoved removed\n"
    assert (newest / "changed").read_text() == "old"
    assert (newest / "removed").read_text() == "old"
    assert not (newest / "added").exists()
    assert (backup.source.location / "changed").read_text() == "new"


@pytest.mark.parametrize("relative", [False, True])
def test_diff_compares_reference_directory_without_accessing_source(
    backup, capsys, monkeypatch, tmp_path, relative
):
    config = config_for(backup)
    newest = backup.artifacts()[0].representation.path
    (newest / "changed").write_text("old")
    (newest / "removed").write_text("old")
    reference = tmp_path / "reference tree"
    reference.mkdir()
    (reference / "changed").write_text("new")
    (reference / "added").write_text("new")
    monkeypatch.setattr(
        backup.source, "cap_source", mock.Mock(side_effect=AssertionError("Must not read source"))
    )
    monkeypatch.chdir(tmp_path)
    path = reference.name if relative else str(reference)

    assert DiffSubcommand().main(config, arguments("home", "newest", "--against-dir", path)) == 0
    assert capsys.readouterr().out == "added   added\nchanged changed\nremoved removed\n"
    assert (newest / "changed").read_text() == "old"
    assert (newest / "removed").read_text() == "old"
    assert not (newest / "added").exists()
    assert (reference / "changed").read_text() == "new"
    assert not (reference / "removed").exists()


@pytest.mark.parametrize("exists", [False, True])
def test_diff_rejects_invalid_reference_before_extracting(backup, monkeypatch, tmp_path, exists):
    config = config_for(backup)
    reference = tmp_path / "not-a-directory"
    if exists:
        reference.write_text("file")
    extract = mock.Mock(side_effect=AssertionError("Must validate directory before extraction"))
    monkeypatch.setattr("yaesm.subcommand.diffsubcommand.detransform", extract)

    with pytest.raises(DiffError) as caught:
        DiffSubcommand().main(config, arguments("home", "newest", "--against-dir", str(reference)))
    assert str(reference) in caught.value.format()
    extract.assert_not_called()


@pytest.mark.parametrize("against_dir", [False, True])
@pytest.mark.parametrize("target", ["home", "old-home", "group", "@all", "home,old-home"])
def test_diff_uses_shared_selection_and_labels_multiple_results(
    backup, capsys, tmp_path, target, against_dir
):
    config = config_for(backup)
    reference = tmp_path / "reference" if against_dir else backup.source.location
    reference.mkdir(exist_ok=True)
    (reference / "added").write_text("new")
    options = ("--against-dir", str(reference)) if against_dir else ()

    assert DiffSubcommand().main(config, arguments(target, *options)) == 0

    expected = "".join(
        f"{artifact.representation.path}:\nadded   added\n" for artifact in backup.artifacts()
    )
    assert capsys.readouterr().out == expected


def test_diff_filters_schedule_alias_before_newest_and_deduplicates_queries(backup, capsys):
    config = config_for(backup)
    (backup.source.location / "added").write_text("new")
    oldest = backup.artifacts()[-1].representation.path
    (oldest / "removed").write_text("old")

    assert (
        DiffSubcommand().main(config, arguments("home", "newest", "-s", "daily", "-q", "oldest"))
        == 0
    )
    assert capsys.readouterr().out == "added   added\nremoved removed\n"


def test_diff_null_separates_paths_containing_newlines(backup, capsys):
    config = config_for(backup)
    (backup.source.location / "new\nfile").write_text("new")

    assert DiffSubcommand().main(config, arguments("home", "newest", "-0")) == 0
    assert capsys.readouterr().out == "added   new\nfile\0"


@pytest.mark.parametrize("against_dir", [False, True])
def test_diff_no_matches_does_not_access_source(backup, capsys, monkeypatch, against_dir):
    config = config_for(backup)
    source = mock.Mock(side_effect=AssertionError("Must not access the source"))
    monkeypatch.setattr(backup.source, "cap_source", source)

    options = ("--against-dir", "missing-reference") if against_dir else ()
    assert DiffSubcommand().main(config, arguments("home", "after", "2099-01-01", *options)) == 0
    assert capsys.readouterr().out == ""


def test_diff_identical_trees_produce_no_output(backup, capsys):
    assert DiffSubcommand().main(config_for(backup), arguments("home")) == 0
    assert capsys.readouterr().out == ""


def test_diff_validates_all_queries_before_listing(backup):
    config = config_for(backup)
    with pytest.raises(DiffError) as caught:
        DiffSubcommand().main(config, arguments("home", "newest", "-q", "after"))
    assert isinstance(caught.value.__cause__, ArtifactQueryError)
    backup.destination.cap_list.assert_not_called()


def test_diff_excludes_backup_storage_inside_source(tmp_path, capsys):
    source = tmp_path / "source"
    storage = source / "backups"
    storage.mkdir(parents=True)
    artifact = storage / "yaesm.home.hourly.2026_08_20_12:00.p0000"
    artifact.mkdir()
    backup = Backup("home", DirectoryDriver(source), RsyncDriver(storage))

    assert DiffSubcommand().main(Config({}, {"home": backup}), arguments("home")) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("remote", [False, True])
def test_diff_reports_unsupported_sources(backup, remote):
    source = DirectoryDriver(
        backup.source.location, SSHTarget("ssh://example.invalid", Path("/key"))
    )
    if not remote:
        source = mock.Mock()
        source.cap_source.return_value = Representation()
    backup = dataclasses.replace(backup, source=source)
    with pytest.raises(DiffError) as caught:
        DiffSubcommand().main(config_for(backup), arguments("home", "newest"))
    assert "local directory source" in caught.value.format()


@pytest.mark.parametrize("against_dir", [False, True])
def test_diff_requires_reference_directory_for_backup_sources(backup, capsys, against_dir):
    config = config_for(backup)
    copy = dataclasses.replace(backup, name="copy", source=BackupSource("home"), previous_names=())
    config = Config({}, {"home": backup, "copy": copy})
    artifacts = backup.destination.cap_list.return_value
    backup.destination.cap_list.side_effect = lambda _name: artifacts
    if against_dir:
        parsed = arguments("copy", "newest", "--against-dir", str(backup.source.location))
        assert DiffSubcommand().main(config, parsed) == 0
        assert capsys.readouterr().out == ""
        return
    with pytest.raises(DiffError) as caught:
        DiffSubcommand().main(config, arguments("copy", "newest"))
    assert "requires a directory source" in caught.value.format()


@pytest.mark.parametrize("remote", [False, True])
def test_diff_reports_unsupported_artifacts(backup, remote):
    config = config_for(backup)
    artifact = backup.artifacts()[0]
    representation = (
        RsyncTree(Path("/must-not-read"), SSHTarget("ssh://example.invalid", Path("/key")))
        if remote
        else Representation()
    )
    backup.destination.cap_list.side_effect = lambda name: (
        (dataclasses.replace(artifact, representation=representation),) if name == "home" else ()
    )

    with pytest.raises(DiffError) as caught:
        DiffSubcommand().main(config, arguments("home", "newest"))
    assert (
        "local trees" if remote else "does not provide a directory tree"
    ) in caught.value.format()


def test_diff_reports_unreadable_files(backup):
    config = config_for(backup)
    (backup.artifacts()[0].representation.path / "file").write_text("old")
    (backup.source.location / "file").write_text("new")
    with (
        mock.patch("builtins.open", side_effect=PermissionError("cannot read file")),
        pytest.raises(DiffError, match="cannot compare backup 'home'.*cannot read file"),
    ):
        DiffSubcommand().main(config, arguments("home", "newest"))


@pytest.mark.parametrize("against_dir", [False, True])
@pytest.mark.parametrize("failure", [None, "read", "output"])
@pytest.mark.parametrize("scratch_inside_source", [False, True])
def test_diff_compares_tar_archives_and_cleans_up(
    tmp_path, capsys, failure, scratch_inside_source, against_dir
):
    source, storage = tmp_path / "source", tmp_path / "storage"
    source.mkdir()
    storage.mkdir()
    scratch = (source if scratch_inside_source else tmp_path) / "scratch"
    scratch.mkdir()
    (source / "file").write_text("old")
    source_driver = DirectoryDriver(source)
    destination = FileDriver(storage)
    transforms = (TarDriver(tmpdir=scratch),)
    Pipeline(source_driver, destination, transforms).execute(
        BackupOperation("home", "hourly", datetime(2026, 8, 20, 12))
    )
    (source / "file").write_text("new")
    backup = Backup("home", source_driver, destination, transforms)
    config = Config({}, {"home": backup})
    options = ("--against-dir", str(source)) if against_dir else ()
    parsed = arguments("home", "newest", *options)
    if failure == "read":
        with (
            mock.patch("builtins.open", side_effect=PermissionError("cannot read file")),
            pytest.raises(DiffError, match="cannot read file"),
        ):
            DiffSubcommand().main(config, parsed)
    elif failure == "output":
        with (
            mock.patch("builtins.print", side_effect=BrokenPipeError("cannot write output")),
            pytest.raises(DiffError, match="cannot write output"),
        ):
            DiffSubcommand().main(config, parsed)
    else:
        assert DiffSubcommand().main(config, parsed) == 0
        assert capsys.readouterr().out == "changed file\n"
    assert list(scratch.iterdir()) == []


@pytest.fixture
def trees(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    return PathTree(before), PathTree(after)


def test_reports_content_changes_in_relative_path_order(trees):
    before, after = trees
    for tree in trees:
        (tree.path / "nested").mkdir()
        (tree.path / "unchanged").write_bytes(b"\x00\xff")
        (tree.path / "nested" / "changed").write_bytes(b"old")
    (before.path / "removed").write_text("old")
    (after.path / ".added").write_text("new")
    changed = after.path / "nested" / "changed"
    changed.write_bytes(b"new")
    stamp = (before.path / "nested" / "changed").stat()
    os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    (after.path / "unchanged").chmod(0o400)
    os.utime(after.path / "unchanged", (1, 1))

    assert list(compare_trees(before, after)) == [
        ("added", Path(".added")),
        ("changed", Path("nested/changed")),
        ("removed", Path("removed")),
    ]


def test_includes_one_sided_directories_and_their_contents(trees):
    before, after = trees
    (before.path / "old" / "empty").mkdir(parents=True)
    (before.path / "old" / "file").write_text("old")
    (after.path / "new" / "empty").mkdir(parents=True)
    (after.path / "new" / "file").write_text("new")

    assert list(compare_trees(before, after)) == [
        ("added", Path("new")),
        ("added", Path("new/empty")),
        ("added", Path("new/file")),
        ("removed", Path("old")),
        ("removed", Path("old/empty")),
        ("removed", Path("old/file")),
    ]


def test_reports_type_changes_once_without_descending(trees):
    before, after = trees
    (before.path / "entry").write_text("old")
    (after.path / "entry").mkdir()
    (after.path / "entry" / "child").write_text("new")

    assert list(compare_trees(before, after)) == [("changed", Path("entry"))]
    assert list(compare_trees(after, before)) == [("changed", Path("entry"))]


def test_compares_symlink_targets_without_following_them(trees):
    before, after = trees
    for tree in trees:
        (tree.path / "loop").symlink_to(".")
        (tree.path / "broken").symlink_to("missing")
        (tree.path / "link").symlink_to("target-a")
    (after.path / "link").unlink()
    (after.path / "link").symlink_to("target-b")
    (before.path / "type").symlink_to("missing")
    (after.path / "type").write_text("missing")
    (after.path / "new-link").symlink_to(".")

    assert list(compare_trees(before, after)) == [
        ("changed", Path("link")),
        ("added", Path("new-link")),
        ("changed", Path("type")),
    ]


def test_skips_excluded_subtrees_on_both_sides(trees):
    before, after = trees
    for tree in trees:
        (tree.path / "ignored").mkdir()
        (tree.path / "nested").mkdir()
    (before.path / "ignored" / "old").write_text("old")
    (after.path / "ignored" / "new").write_text("new")
    (after.path / "nested" / "skip").write_text("new")
    (after.path / "ignored-prefix").write_text("included")
    before = PathTree(before.path, excluded_paths=(Path("ignored"),))
    after = PathTree(after.path, excluded_paths=(Path("nested/skip"),))
    scandir = os.scandir

    def scan(path):
        assert Path(path).name != "ignored", "Excluded directories must not be read"
        return scandir(path)

    with mock.patch("yaesm.subcommand.diffsubcommand.os.scandir", side_effect=scan):
        assert list(compare_trees(before, after)) == [("added", Path("ignored-prefix"))]


def test_skips_reads_for_different_sizes_and_shared_inodes(trees):
    before, after = trees
    (before.path / "size").write_bytes(b"a")
    (after.path / "size").write_bytes(b"bb")
    (before.path / "hard-link").write_bytes(b"same")
    os.link(before.path / "hard-link", after.path / "hard-link")

    with mock.patch("builtins.open", side_effect=AssertionError("Unnecessary content read")):
        assert list(compare_trees(before, after)) == [("changed", Path("size"))]


def test_compares_content_past_first_chunk(trees):
    before, after = trees
    content = b"x" * (2 * 1024 * 1024)
    (before.path / "large").write_bytes(content + b"a")
    (after.path / "large").write_bytes(content + b"a")
    assert list(compare_trees(before, after)) == []

    (after.path / "large").write_bytes(content + b"b")
    assert list(compare_trees(before, after)) == [("changed", Path("large"))]


def test_does_not_reuse_results_for_same_size_and_timestamp(trees):
    before, after = trees
    for tree in trees:
        (tree.path / "file").write_bytes(b"old")
        os.utime(tree.path / "file", (1, 1))
    assert list(compare_trees(before, after)) == []

    (after.path / "file").write_bytes(b"new")
    os.utime(after.path / "file", (1, 1))
    assert list(compare_trees(before, after)) == [("changed", Path("file"))]


def test_never_opens_special_files(trees):
    before, after = trees
    for tree in trees:
        os.mkfifo(tree.path / "pipe")

    with mock.patch("builtins.open", side_effect=AssertionError("Must not read a FIFO")):
        assert list(compare_trees(before, after)) == []


@pytest.mark.parametrize("side", [0, 1])
def test_missing_tree_is_an_error_not_an_empty_directory(trees, side):
    trees[side].path.rmdir()
    with pytest.raises(FileNotFoundError):
        list(compare_trees(*trees))


@pytest.mark.parametrize("side", [0, 1])
def test_rejects_remote_trees_before_accessing_local_paths(trees, side):
    trees = list(trees)
    trees[side] = PathTree(Path("/must-not-read"), SSHTarget("ssh://example.invalid", Path("/key")))
    with pytest.raises(DiffError, match="local trees"):
        list(compare_trees(*trees))
