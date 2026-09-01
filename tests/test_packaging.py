"""Tests for service-manager packaging."""

from pathlib import Path


def test_systemd_service_has_a_finite_forced_shutdown_fallback():
    service = Path("packaging/systemd/yaesm.service").read_text()

    assert "KillMode=mixed" in service.splitlines()
    assert "TimeoutStopSec=infinity" not in service.splitlines()
