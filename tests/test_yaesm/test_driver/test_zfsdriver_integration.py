"""Integration tests for yaesm.driver.zfsdriver."""

import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta

import pytest

import yaesm.ty as ty
from yaesm.backup import Backup, DriverSource
from yaesm.check import CheckRole
from yaesm.driver.zfsdriver import ZFSDriver
from yaesm.representation import DataProperty


@pytest.fixture
def zfs_pools(tmp_path: ty.Path) -> ty.Iterator[tuple[str, str]]:
    if shutil.which("zfs") is None or shutil.which("zpool") is None:
        pytest.skip("ZFS is not installed")
    if os.geteuid() != 0:
        pytest.skip("ZFS integration tests require root")

    prefix = f"yaesm_test_{uuid.uuid4().hex[:10]}"
    pools = (f"{prefix}_source", f"{prefix}_destination")
    created = []
    try:
        for pool in pools:
            image = tmp_path / f"{pool}.img"
            with image.open("wb") as file:
                file.truncate(256 * 1024 * 1024)
            result = subprocess.run(
                ("zpool", "create", "-f", "-m", "none", pool, str(image)),
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                pytest.fail(f"could not create temporary ZFS pool: {result.stderr.strip()}")
            created.append(pool)
        yield pools
    finally:
        for pool in reversed(created):
            subprocess.run(
                ("zpool", "destroy", "-f", pool),
                capture_output=True,
                check=False,
            )


def test_zfs_full_incremental_and_lifecycle(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "source"
    _run("zfs", "create", "-o", f"mountpoint={source_path}", source_dataset)

    source_driver = ZFSDriver(source_dataset)
    destination_driver = ZFSDriver(destination_dataset)
    backup = Backup(
        "example",
        DriverSource(source_driver),
        destination_driver,
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    (source_path / "content").write_text("first")
    first = backup.execute("manual", created_at)
    (source_path / "content").write_text("second")
    second = backup.execute("manual", created_at + timedelta(minutes=1))

    assert destination_driver.cap_list("example") == (second, first)
    assert _snapshots(source_dataset) == {f"{source_dataset}@{second.name}"}

    destination_path = tmp_path / "destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_text() == "second"

    destination_driver.cap_delete((first, second))
    assert destination_driver.cap_list("example") == ()


def test_zfs_checks_existing_source_and_new_destination(
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    _run("zfs", "create", "-u", source_dataset)

    source_results = tuple(
        check.run() for check in ZFSDriver(source_dataset).check(CheckRole.SOURCE)
    )
    destination_results = tuple(
        check.run()
        for check in ZFSDriver(f"{destination_pool}/backup").check(CheckRole.DESTINATION)
    )

    assert all(result.passed for result in (*source_results, *destination_results))


def test_zfs_raw_encrypted_full_and_incremental(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "encrypted-source"
    key = tmp_path / "zfs-key"
    key.write_text("integration-test-passphrase")
    key.chmod(0o600)
    _run(
        "zfs",
        "create",
        "-o",
        "encryption=aes-256-gcm",
        "-o",
        "keyformat=passphrase",
        "-o",
        f"keylocation={key.as_uri()}",
        "-o",
        f"mountpoint={source_path}",
        source_dataset,
    )

    backup = Backup(
        "encrypted",
        DriverSource(ZFSDriver(source_dataset, encryption=True)),
        ZFSDriver(destination_dataset),
        requirements=frozenset({DataProperty.ENCRYPTED}),
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    (source_path / "content").write_text("first")
    backup.execute("manual", created_at)
    (source_path / "content").write_text("second")
    second = backup.execute("manual", created_at + timedelta(minutes=1))

    assert second.representation.encrypted is True
    assert _property(destination_dataset, "encryption") == "aes-256-gcm"
    if _property(destination_dataset, "keystatus") != "available":
        _run("zfs", "load-key", "-L", key.as_uri(), destination_dataset)

    destination_path = tmp_path / "encrypted-destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_text() == "second"


def test_zfs_preserves_native_compression_by_default(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "compressed-source"
    _run(
        "zfs",
        "create",
        "-o",
        "compression=lz4",
        "-o",
        f"mountpoint={source_path}",
        source_dataset,
    )

    backup = Backup(
        "compressed",
        DriverSource(ZFSDriver(source_dataset)),
        ZFSDriver(destination_dataset),
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    content = source_path / "content"
    content.write_bytes(b"a" * 1024 * 1024)
    backup.execute("manual", created_at)
    content.write_bytes(b"b" * 1024 * 1024)
    backup.execute("manual", created_at + timedelta(minutes=1))

    assert float(_property(destination_dataset, "refcompressratio").removesuffix("x")) > 1

    destination_path = tmp_path / "compressed-destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_bytes() == b"b" * 1024 * 1024


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=True, text=True)


def _property(dataset: str, name: str) -> str:
    return _run("zfs", "get", "-H", "-o", "value", name, dataset).stdout.strip()


def _snapshots(dataset: str) -> set[str]:
    output = _run(
        "zfs",
        "list",
        "-H",
        "-t",
        "snapshot",
        "-o",
        "name",
        "-d",
        "1",
        dataset,
    ).stdout
    return set(output.splitlines())


def _mount(dataset: str, path: ty.Path) -> None:
    _run("zfs", "set", f"mountpoint={path}", dataset)
    if _property(dataset, "mounted") != "yes":
        _run("zfs", "mount", dataset)
