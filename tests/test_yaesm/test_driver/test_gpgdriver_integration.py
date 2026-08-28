"""Integration tests for yaesm.driver.gpgdriver."""

import dataclasses
import shutil
import subprocess
from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation, DriverSource
from yaesm.check import CheckRole
from yaesm.command import CommandError, CommandRunner
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.driver.driverbase import DriverBase
from yaesm.driver.gpgdriver import GPGDriver, GPGStream
from yaesm.pipeline import Pipeline
from yaesm.representation import ByteStream, CommandStream, DataProperty, Representation


@dataclasses.dataclass(frozen=True)
class _EncryptedFile(Representation):
    path: ty.Path


class _FileDestination(DriverBase):
    def __init__(self, location: ty.Path) -> None:
        super().__init__()
        self.location = location

    @classmethod
    def name(cls) -> str:
        return "test-file"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact[_EncryptedFile]:
        assert isinstance(source, GPGStream)
        destination = self.location / operation.artifact_name
        self.runner.pipeline(
            (
                *source.commands[:-1],
                (*source.commands[-1], "--output", str(destination)),
            )
        )
        return BackupArtifact(operation, _EncryptedFile(destination))


@pytest.mark.parametrize("armored", [False, True], ids=("binary-key", "armored-key"))
def test_gpg_checks_exported_public_key(tmp_path: ty.Path, armored: bool) -> None:
    _require_gpg()
    identity = "yaesm check test <yaesm-check@example.invalid>"
    gpg_home = tmp_path / "gnupg"
    public_key = tmp_path / ("public-key.asc" if armored else "public-key.gpg")
    _generate_key(gpg_home, identity)
    _export_public_key(gpg_home, identity, public_key, armored)

    results = tuple(check.run() for check in GPGDriver(public_key).check(CheckRole.TRANSFORM))

    assert all(result.passed for result in results)


@pytest.mark.parametrize("armored", [False, True], ids=("binary-key", "armored-key"))
def test_gpg_encrypts_and_restores_btrfs_pipeline(
    tmp_path: ty.Path,
    btrfs_filesystem: ty.Path,
    armored: bool,
) -> None:
    _require_gpg()
    identity = "yaesm integration test <yaesm@example.invalid>"
    gpg_home = tmp_path / "gnupg"
    _generate_key(gpg_home, identity)
    public_key = tmp_path / ("public-key.asc" if armored else "public-key.gpg")
    _export_public_key(gpg_home, identity, public_key, armored)

    source = btrfs_filesystem / "source"
    destination = btrfs_filesystem / "destination"
    create = subprocess.run(
        ("btrfs", "subvolume", "create", str(source)),
        capture_output=True,
        check=False,
    )
    if create.returncode:
        pytest.skip("Btrfs subvolumes cannot be created in the test directory")
    destination.mkdir()

    restored = None
    try:
        (source / "content").write_text("encrypted backup content")
        operation = BackupOperation("example", "manual", datetime(2026, 8, 27, 12, 30))
        artifact = Pipeline(
            DriverSource(BtrfsDriver(source)),
            _FileDestination(tmp_path),
            (GPGDriver(public_key),),
            requirements=(DataProperty.ENCRYPTED,),
        ).execute(operation)
        encrypted_backup = artifact.representation.path

        assert encrypted_backup.stat().st_size > 0
        assert b"encrypted backup content" not in encrypted_backup.read_bytes()

        wrong_gpg_home = tmp_path / "wrong-gnupg"
        _generate_key(wrong_gpg_home, "wrong key <wrong@example.invalid>")
        with pytest.raises(CommandError):
            CommandRunner().run(
                (
                    "gpg",
                    "--batch",
                    "--no-tty",
                    "--homedir",
                    str(wrong_gpg_home),
                    "--decrypt",
                    str(encrypted_backup),
                ),
                capture_output=True,
            )

        CommandRunner().pipeline(
            (
                (
                    "gpg",
                    "--batch",
                    "--no-tty",
                    "--homedir",
                    str(gpg_home),
                    "--decrypt",
                    str(encrypted_backup),
                ),
                ("btrfs", "receive", str(destination)),
            )
        )
        restored = next(destination.iterdir())
        assert (restored / "content").read_text() == "encrypted backup content"
    finally:
        for path in (restored, source):
            if path is not None and path.exists():
                subprocess.run(
                    ("btrfs", "subvolume", "delete", str(path)),
                    capture_output=True,
                    check=False,
                )


@pytest.mark.parametrize("malformed", [False, True], ids=("missing", "malformed"))
def test_gpg_rejects_invalid_public_key(tmp_path: ty.Path, malformed: bool) -> None:
    _require_gpg()
    public_key = tmp_path / "public-key.asc"
    if malformed:
        public_key.write_text("not an OpenPGP public key")

    result = GPGDriver(public_key).check(CheckRole.TRANSFORM)[1].run()

    assert result.passed is False
    assert result.stderr

    encrypted_backup = tmp_path / "backup.gpg"
    stream = GPGDriver(public_key).cap_encrypt(CommandStream((("printf", "content"),)))

    with pytest.raises(CommandError) as error:
        CommandRunner().pipeline(
            (
                *stream.commands[:-1],
                (*stream.commands[-1], "--output", str(encrypted_backup)),
            )
        )

    assert error.value.command[0] == "gpg"


def _require_gpg() -> None:
    if shutil.which("gpg") is None:
        pytest.skip("GnuPG is not installed")


def _generate_key(gpg_home: ty.Path, identity: str) -> None:
    gpg_home.mkdir(mode=0o700)
    subprocess.run(
        (
            "gpg",
            "--batch",
            "--homedir",
            str(gpg_home),
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            identity,
            "rsa2048",
            "encr",
            "0",
        ),
        capture_output=True,
        check=True,
    )


def _export_public_key(
    gpg_home: ty.Path,
    identity: str,
    public_key: ty.Path,
    armored: bool,
) -> None:
    command = ["gpg", "--batch", "--homedir", str(gpg_home)]
    if armored:
        command.append("--armor")
    command.extend(("--export", identity))
    with public_key.open("wb") as output:
        subprocess.run(
            command,
            check=True,
            stderr=subprocess.PIPE,
            stdout=output,
        )
