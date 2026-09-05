"""Tests for yaesm.representation."""

from pathlib import Path

from yaesm.command import CommandStage
from yaesm.representation import (
    BlockDevice,
    ByteStream,
    CommandStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    PathTree,
    ReadableTree,
    Representation,
    UncompressedStream,
)
from yaesm.ssh import SSHTarget


def test_representation_types_share_one_root():
    assert ReadableTree.__bases__ == (Representation,)
    assert PathTree.__bases__ == (ReadableTree,)
    assert BlockDevice.__bases__ == (Representation,)
    assert ByteStream.__bases__ == (Representation,)
    assert CommandStream.__bases__ == (ByteStream,)
    assert UncompressedStream.__bases__ == (CommandStream,)
    assert CompressedStream.__bases__ == (CommandStream,)
    assert EncryptedStream.__bases__ == (CommandStream,)


def test_representations_have_no_suffix_by_default():
    assert Representation.suffix == ""


def test_path_tree_has_local_or_remote_location(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert PathTree(tmp_path).path == tmp_path
    assert PathTree(tmp_path, target).ssh is target


def test_path_tree_contains_ordered_excluded_paths(tmp_path):
    paths = (Path("first"), Path("second"))

    assert PathTree(tmp_path, excluded_paths=paths).excluded_paths == paths


def test_command_stream_contains_stages():
    stages = (CommandStage(("first",)), CommandStage(("second", "argument")))
    stream = CommandStream(stages)

    assert stream.stages == stages
    assert stream.suffixes == ()


def test_command_stream_contains_ordered_suffixes():
    stream = CommandStream(suffixes=(".tar", ".zst", ".gpg"))

    assert stream.suffixes == (".tar", ".zst", ".gpg")


def test_data_properties():
    assert {property_.value for property_ in DataProperty} == {
        "snapshot",
        "archived",
        "compressed",
        "encrypted",
    }
