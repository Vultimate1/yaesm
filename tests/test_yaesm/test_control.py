"""Tests for yaesm.control."""

import json
import logging
import socket
import stat
from contextlib import contextmanager
from unittest import mock

import pytest

import yaesm.control as control_module
import yaesm.ty as ty
from yaesm.control import (
    ControlError,
    ControlHandler,
    ControlMessage,
    ControlServer,
    send_request,
)
from yaesm.errors import YaesmError


@contextmanager
def running_server(path: ty.Path, handler: ControlHandler) -> ty.Iterator[None]:
    server = ControlServer(path, handler)
    server.start()
    try:
        yield
    finally:
        server.stop()


def test_control_error_is_expected_error():
    assert issubclass(ControlError, YaesmError)


def test_control_server_streams_messages(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "run" / "control.sock"
    requests = []

    def handler(request: ty.Mapping[str, object]) -> tuple[ControlMessage, ...]:
        requests.append(request)
        return (
            {"type": "log", "message": "starting backup"},
            {"type": "result", "ok": True, "request_id": None},
        )

    with running_server(path, handler):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        responses = list(send_request(path, {"command": "backup"}))

    assert requests == [{"command": "backup"}]
    assert responses == [
        {"type": "log", "message": "starting backup"},
        {"type": "result", "ok": True, "request_id": None},
    ]
    assert f"control socket listening at {path}" in caplog.messages
    assert not path.exists()


def test_control_server_reports_handler_error(tmp_path):
    def handler(_request: ty.Mapping[str, object]) -> tuple[ControlMessage, ...]:
        raise ControlError("request failed")

    path = tmp_path / "control.sock"
    with running_server(path, handler):
        responses = list(send_request(path, {}))

    assert responses == [
        {
            "type": "result",
            "ok": False,
            "error": "request failed",
            "error_logged": False,
            "request_id": None,
        }
    ]


def test_control_server_hides_unexpected_handler_error(tmp_path, caplog):
    def handler(_request: ty.Mapping[str, object]) -> tuple[ControlMessage, ...]:
        raise RuntimeError("secret detail")

    path = tmp_path / "control.sock"
    with running_server(path, handler):
        responses = list(send_request(path, {}))

    assert responses == [
        {
            "type": "result",
            "ok": False,
            "error": "internal control error",
            "error_logged": False,
            "request_id": None,
        }
    ]
    assert "control request failed" in caplog.messages


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"not JSON\n", "invalid control request"),
        (b"[]\n", "control request must be an object"),
    ],
)
def test_control_server_rejects_invalid_requests(tmp_path, data, error):
    path = tmp_path / "control.sock"
    with (
        running_server(path, lambda _request: ()),
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client,
    ):
        client.connect(str(path))
        client.sendall(data)
        response = json.loads(client.makefile("rb").readline())

    assert response["type"] == "result"
    assert response["ok"] is False
    assert error in response["error"]


def test_control_request_reports_scheduler_not_running(tmp_path):
    path = tmp_path / "missing.sock"

    with pytest.raises(ControlError, match="could not connect to the yaesm scheduler") as raised:
        list(send_request(path, {"command": "backup"}))

    assert str(path) in str(raised.value)


def test_control_request_reports_stale_scheduler_socket(tmp_path):
    path = tmp_path / "control.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale_socket:
        stale_socket.bind(str(path))

    with pytest.raises(ControlError, match="could not connect to the yaesm scheduler"):
        list(send_request(path, {"command": "backup"}))


def test_control_request_requires_result(tmp_path):
    path = tmp_path / "control.sock"
    with (
        running_server(path, lambda _request: ()),
        pytest.raises(ControlError, match="control socket closed without a result"),
    ):
        list(send_request(path, {"command": "backup"}))


def test_control_server_preserves_non_socket_path(tmp_path):
    path = tmp_path / "control.sock"
    path.write_text("important")

    with pytest.raises(ControlError, match="control socket path is not a socket"):
        ControlServer(path, lambda _request: ())

    assert path.read_text() == "important"


def test_control_server_cleans_up_start_failure(tmp_path, monkeypatch):
    path = tmp_path / "control.sock"
    monkeypatch.setattr(
        control_module.os,
        "chmod",
        mock.Mock(side_effect=PermissionError("denied")),
    )

    with pytest.raises(ControlError, match=f"could not open control socket {path}"):
        ControlServer(path, lambda _request: ())

    assert not path.exists()
