"""Tests for yaesm.logging."""

import logging
import logging.handlers
from unittest import mock
from uuid import UUID

from yaesm.logging import RequestFilter, configure, request_id


def test_configure_uses_uniform_format():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure(logging.DEBUG)

    arguments = dict(basic_config.call_args.kwargs)
    handlers = arguments.pop("handlers")
    assert arguments == {
        "level": logging.DEBUG,
        "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "force": True,
    }
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)


def test_configure_defaults_to_info():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure()

    assert basic_config.call_args.kwargs["level"] == logging.INFO


def test_configure_uses_syslog():
    handler = mock.Mock()
    with (
        mock.patch.object(logging.handlers, "SysLogHandler", return_value=handler) as syslog,
        mock.patch.object(logging, "basicConfig") as basic_config,
    ):
        configure(logging.WARNING, syslog_address="/var/run/log")

    syslog.assert_called_once_with(address="/var/run/log")
    assert basic_config.call_args.kwargs["handlers"] == [handler]


def test_configure_combines_destinations(tmp_path):
    stream_handler = mock.Mock()
    file_handler = mock.Mock()
    syslog_handler = mock.Mock()
    path = tmp_path / "yaesm.log"
    with (
        mock.patch.object(logging, "StreamHandler", return_value=stream_handler),
        mock.patch.object(logging, "FileHandler", return_value=file_handler) as file,
        mock.patch.object(
            logging.handlers,
            "SysLogHandler",
            return_value=syslog_handler,
        ),
        mock.patch.object(logging, "basicConfig") as basic_config,
    ):
        configure(
            stderr=True,
            logfile=path,
            syslog_address="/dev/log",
        )

    file.assert_called_once_with(path, encoding="utf-8")
    assert basic_config.call_args.kwargs["handlers"] == [
        stream_handler,
        file_handler,
        syslog_handler,
    ]


def test_request_filter_matches_current_request():
    first = UUID("11111111-1111-1111-1111-111111111111")
    second = UUID("22222222-2222-2222-2222-222222222222")
    request_filter = RequestFilter(first)
    record = logging.LogRecord("test", logging.INFO, "", 0, "message", (), None)
    token = request_id.set(first)
    try:
        assert request_filter.filter(record)
        assert not RequestFilter(second).filter(record)
    finally:
        request_id.reset(token)

    assert not request_filter.filter(record)
