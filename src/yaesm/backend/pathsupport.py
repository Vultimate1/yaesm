"""Shared behavior for backends that use filesystem paths."""

import shutil
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm import config
from yaesm.backend.backendbase import CheckResult
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class _PathBackend(ty.Protocol):
    src_dir: Path | SSHTarget
    dst_dir: Path | SSHTarget

    @classmethod
    def name(cls) -> str: ...


def config_settings() -> set[str]:
    return config.SrcDirDstDirSchema.valid_settings()


def config_schema() -> vlp.Schema:
    def _apply_to_backend(d: dict) -> dict:
        configure_paths(d["backend"], d["src_dir"], d["dst_dir"])
        return d

    return vlp.Schema(vlp.All(config.SrcDirDstDirSchema.schema(), _apply_to_backend))


def config_schema_extra() -> vlp.Schema:
    return config.SrcDirDstDirSchema.schema_extra()


def configure_paths(
    backend: _PathBackend, src_dir: Path | SSHTarget, dst_dir: Path | SSHTarget
) -> None:
    """Set the source and destination, rejecting different remote endpoints."""
    if (
        isinstance(src_dir, SSHTarget)
        and isinstance(dst_dir, SSHTarget)
        and not src_dir.same_endpoint(dst_dir)
    ):
        raise bckp.BackupError(
            "remote src_dir and dst_dir must use the same SSH user, host, and port"
        )
    backend.src_dir = src_dir
    backend.dst_dir = dst_dir


def backup_type(backend: _PathBackend) -> str:
    if isinstance(backend.src_dir, SSHTarget) and isinstance(backend.dst_dir, SSHTarget):
        return "remote_to_remote"
    if isinstance(backend.src_dir, SSHTarget):
        return "remote_to_local"
    if isinstance(backend.dst_dir, SSHTarget):
        return "local_to_remote"
    return "local_to_local"


def format_locator(backend: _PathBackend, artifact: bckp.BackupArtifact) -> str:
    if isinstance(backend.dst_dir, SSHTarget):
        return str(backend.dst_dir.with_path(Path(artifact.locator)))
    return artifact.locator


def check(backend: _PathBackend) -> list[CheckResult]:
    """Check common preconditions for a path-based backup."""
    results: list[CheckResult] = []

    def add_result(description: str, errors: list[str]) -> CheckResult:
        result = CheckResult(description, tuple(errors))
        results.append(result)
        return result

    src_dir = backend.src_dir
    dst_dir = backend.dst_dir
    sshtarget = src_dir if isinstance(src_dir, SSHTarget) else None
    if isinstance(dst_dir, SSHTarget):
        sshtarget = dst_dir
    ssh_connected = True
    if sshtarget is not None:
        ssh_connected = add_result(
            f"SSH connection to {sshtarget.host}",
            _error_unless(
                sshtarget.can_connect(), f"cannot establish SSH connection to {sshtarget.host}"
            ),
        ).passed

    if isinstance(src_dir, SSHTarget):
        if ssh_connected:
            add_result(
                f"src_dir exists on remote {src_dir.host}: {src_dir.path}",
                _error_unless(
                    src_dir.is_dir(),
                    f"src_dir does not exist on remote {src_dir.host}: {src_dir.path}",
                ),
            )
            add_result(
                f"src_dir is readable on remote {src_dir.host}: {src_dir.path}",
                _error_unless(
                    src_dir.is_readable(),
                    f"src_dir is not readable on remote {src_dir.host}: {src_dir.path}",
                ),
            )
    else:
        add_result(
            f"src_dir exists locally: {src_dir}",
            _error_unless(src_dir.is_dir(), f"src_dir does not exist locally: {src_dir}"),
        )

    if isinstance(dst_dir, SSHTarget):
        if ssh_connected:
            add_result(
                f"dst_dir exists on remote {dst_dir.host}: {dst_dir.path}",
                _error_unless(
                    dst_dir.is_dir(),
                    f"dst_dir does not exist on remote {dst_dir.host}: {dst_dir.path}",
                ),
            )
            add_result(
                f"dst_dir is writable on remote {dst_dir.host}: {dst_dir.path}",
                _error_unless(
                    dst_dir.is_writable(),
                    f"dst_dir is not writable on remote {dst_dir.host}: {dst_dir.path}",
                ),
            )
    else:
        add_result(
            f"dst_dir exists locally: {dst_dir}",
            _error_unless(dst_dir.is_dir(), f"dst_dir does not exist locally: {dst_dir}"),
        )

    if backup_type(backend) != "remote_to_remote":
        add_result(
            f"{backend.name()} is installed locally",
            _error_unless(
                shutil.which(backend.name()) is not None,
                f"required tool not found locally: {backend.name()}",
            ),
        )
    if sshtarget is not None and ssh_connected:
        add_result(
            f"{backend.name()} is installed on remote {sshtarget.host}",
            _error_unless(
                sshtarget.command_exists(backend.name()),
                f"required tool not found on remote {sshtarget.host}: {backend.name()}",
            ),
        )
    return results


def collect(
    backend: _PathBackend,
    backup: bckp.Backup,
    timeframes: list[Timeframe] | None = None,
) -> list[bckp.BackupArtifact]:
    """Collect directory-backed artifacts from newest to oldest."""
    return bckp.path_artifacts_collect(backup, backend.dst_dir, timeframes=timeframes)


def _error_unless(condition: bool, error: str) -> list[str]:
    return [] if condition else [error]
