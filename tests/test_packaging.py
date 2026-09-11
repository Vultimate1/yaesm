"""Tests for service-manager packaging."""

from pathlib import Path


def test_systemd_service_has_a_finite_forced_shutdown_fallback():
    service = Path("packaging/systemd/yaesm.service").read_text()

    assert "KillMode=mixed" in service.splitlines()
    assert "TimeoutStopSec=infinity" not in service.splitlines()


def test_systemd_service_uses_compact_stderr_logging():
    service = Path("packaging/systemd/yaesm.service").read_text()
    exec_start = next(line for line in service.splitlines() if line.startswith("ExecStart="))

    assert "--log-stderr run --no-stderr-timestamps" in exec_start
