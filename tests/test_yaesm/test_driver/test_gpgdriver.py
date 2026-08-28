"""Tests for yaesm.driver.gpgdriver."""

import dataclasses
import os
import shutil
import subprocess
from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation, DriverSource
from yaesm.check import CheckResult, CheckRole
from yaesm.command import Command, CommandError, CommandResult, CommandRunner
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.driver.driverbase import DriverBase
from yaesm.driver.gpgdriver import GPGDriver, GPGStream
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline
from yaesm.representation import (
    ByteStream,
    CommandStream,
    DataProperty,
    EncryptedStream,
    Representation,
)


class RecordingRunner(CommandRunner):
    def __init__(self, *results: CommandResult | CommandError) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], bool, bool]] = []

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(str(argument) for argument in command)
        self.calls.append((normalized, capture_output, check))
        result = self.results.pop(0)
        if isinstance(result, CommandError):
            raise result
        return result


def test_name():
    assert GPGDriver.name() == "gpg"


def test_config_schema_accepts_shorthand(tmp_path):
    public_key = tmp_path / "backup-key.asc"

    assert GPGDriver.config_schema()(public_key) == {"public_key": public_key}


def test_config_schema_accepts_mapping(tmp_path):
    public_key = tmp_path / "backup-key.asc"

    assert GPGDriver.config_schema()({"public_key": str(public_key)}) == {"public_key": public_key}


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_config_schema_rejects_invalid_public_key_type(value):
    with pytest.raises(vlp.Invalid, match="public_key must be a path"):
        GPGDriver.config_schema()({"public_key": value})


def test_config_schema_rejects_relative_public_key():
    with pytest.raises(vlp.Invalid, match="public_key must be an absolute path"):
        GPGDriver.config_schema()("backup-key.asc")


@pytest.mark.parametrize("config", [{}, {"public_key": "/key", "unknown": True}])
def test_config_schema_rejects_invalid_mapping(config):
    with pytest.raises(vlp.Invalid):
        GPGDriver.config_schema()(config)


def test_config_schema_output_constructs_driver(tmp_path):
    config = GPGDriver.config_schema()(tmp_path / "backup-key.asc")

    assert GPGDriver(**config).public_key == tmp_path / "backup-key.asc"


def test_constructor_rejects_invalid_public_key_type():
    with pytest.raises(YaesmValueError, match="public_key must be a path"):
        GPGDriver(ty.cast(ty.Path, None))


def test_constructor_rejects_relative_public_key():
    with pytest.raises(YaesmValueError, match="public_key must be an absolute path"):
        GPGDriver(ty.Path("backup-key.asc"))


def test_checks_are_deferred_and_capture_output(tmp_path, monkeypatch):
    public_key = tmp_path / "backup-key.asc"
    runner = RecordingRunner(
        CommandResult("gpg 2.4\n", "", (0,)),
        CommandResult("", "gpg warning\n", (0,)),
    )
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(public_key)
    checks = driver.check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == (
        "gpg is installed",
        f"public key can encrypt data: {public_key}",
    )
    assert runner.calls == []

    executable_result, key_result = (check.run() for check in checks)

    assert executable_result.passed is True
    assert executable_result.stdout == "gpg 2.4\n"
    assert key_result.passed is True
    assert key_result.stdout is None
    assert key_result.stderr == "gpg warning\n"
    assert runner.calls == [
        (("gpg", "--version"), True, False),
        (
            (
                "gpg",
                "--batch",
                "--no-tty",
                "--no-keyring",
                "--compress-algo",
                "none",
                "--recipient-file",
                str(public_key),
                "--output",
                os.devnull,
                "--encrypt",
                str(public_key),
            ),
            True,
            False,
        ),
    ]


def test_key_check_reports_command_failure(tmp_path, monkeypatch):
    public_key = tmp_path / "bad-key.asc"
    runner = RecordingRunner(CommandResult(None, "invalid public key", (2,)))
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(public_key)

    result = driver.check(CheckRole.TRANSFORM)[1].run()

    assert result.passed is False
    assert result.failure == "gpg exited with status 2"
    assert result.stderr == "invalid public key"


def test_key_check_reports_start_failure(tmp_path, monkeypatch):
    public_key = tmp_path / "bad-key.asc"
    error = CommandError(("gpg", "--encrypt"), None, "Permission denied")
    runner = RecordingRunner(error)
    monkeypatch.setattr(command_module, "run", runner.run)

    result = GPGDriver(public_key)._checks(CheckRole.TRANSFORM)[0].run()

    assert result == CheckResult(
        f"public key can encrypt data: {public_key}",
        "could not start gpg",
        stderr="Permission denied",
    )


def test_executable_check_reports_start_failure(tmp_path, monkeypatch):
    error = CommandError(("gpg", "--version"), None, "No such file or directory")
    runner = RecordingRunner(error)
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(tmp_path / "key")

    result = driver.check(CheckRole.TRANSFORM)[0].run()

    assert result.passed is False
    assert result.failure == "could not start gpg"
    assert result.stderr == "No such file or directory"


@pytest.mark.parametrize("role", [CheckRole.SOURCE, CheckRole.DESTINATION])
def test_key_check_is_only_used_for_transform_role(tmp_path, role):
    driver = GPGDriver(tmp_path / "key")
    checks = driver.check(role)

    assert tuple(check.description for check in checks) == ("gpg is installed",)
    assert driver._checks(role) == ()


def test_cap_encrypt_appends_noninteractive_gpg_filter(tmp_path):
    public_key = tmp_path / "backup-key.asc"
    source = CommandStream((("produce", "data"),))

    stream = GPGDriver(public_key).cap_encrypt(source)

    assert stream == GPGStream(
        (
            ("produce", "data"),
            (
                "gpg",
                "--batch",
                "--no-tty",
                "--no-keyring",
                "--compress-algo",
                "none",
                "--recipient-file",
                str(public_key),
                "--encrypt",
            ),
        )
    )


def test_capability_advertises_only_encryption(tmp_path):
    driver = GPGDriver(tmp_path / "backup-key.asc")

    assert driver.capabilities() == {"encrypt"}
    assert driver.capability_metadata("encrypt").adds == {DataProperty.ENCRYPTED}


def test_gpg_stream_is_encrypted_command_stream():
    assert issubclass(GPGStream, EncryptedStream)


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
