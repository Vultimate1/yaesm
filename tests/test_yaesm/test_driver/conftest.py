"""Shared fixtures for driver integration tests."""

import os
import shutil
import subprocess

import pytest

import yaesm.ty as ty


@pytest.fixture
def btrfs_filesystem(tmp_path: ty.Path) -> ty.Iterator[ty.Path]:
    """Provide a temporary Btrfs filesystem when possible."""
    if shutil.which("btrfs") is None or shutil.which("mkfs.btrfs") is None:
        pytest.skip("Btrfs is not installed")
    if (
        subprocess.run(
            ("btrfs", "filesystem", "usage", str(tmp_path)),
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ):
        yield tmp_path
        return
    if os.geteuid() != 0:
        pytest.skip("Btrfs integration tests require root")

    image = tmp_path / "btrfs.img"
    with image.open("wb") as file:
        file.truncate(256 * 1024 * 1024)
    mountpoint = tmp_path / "btrfs"
    mountpoint.mkdir()
    subprocess.run(("mkfs.btrfs", "-f", str(image)), capture_output=True, check=True)
    subprocess.run(
        ("mount", "-o", "loop", str(image), str(mountpoint)),
        capture_output=True,
        check=True,
    )
    try:
        yield mountpoint
    finally:
        subprocess.run(("umount", str(mountpoint)), capture_output=True, check=False)
