"""Tests for yaesm.logging."""

import logging
import logging.handlers
from unittest import mock
from uuid import UUID

from yaesm.logging import RequestFilter, configure, format_duration, request_id


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(65) == "1m 5s"
    assert format_duration(3661) == "1h 1m 1s"


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


def test_configure_reports_logger_names_under_yaesm_namespace():
    with (
        mock.patch.object(logging, "basicConfig"),
        mock.patch.object(logging, "setLogRecordFactory") as set_record_factory,
    ):
        configure()

    factory = set_record_factory.call_args.args[0]
    external = factory(
        "apscheduler.scheduler",
        logging.WARNING,
        "",
        0,
        "message",
        (),
        None,
    )
    internal = factory(
        "yaesm.scheduler",
        logging.WARNING,
        "",
        0,
        "message",
        (),
        None,
    )

    assert external.name == "yaesm.apscheduler.scheduler"
    assert internal.name == "yaesm.scheduler"


def test_configure_formats_interactive_stderr():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure(message_only_stderr=True)

    handler = basic_config.call_args.kwargs["handlers"][0]
    record = logging.LogRecord("test", logging.ERROR, "", 0, "plain error", (), None)
    assert handler.format(record) == "yaesm: plain error"


def test_configure_can_omit_stderr_timestamps():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure(stderr_timestamps=False)

    handler = basic_config.call_args.kwargs["handlers"][0]
    record = logging.LogRecord("yaesm.test", logging.INFO, "", 0, "message", (), None)
    assert handler.format(record) == "INFO yaesm.test: message"


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
            stderr_timestamps=False,
            logfile=path,
            syslog_address="/dev/log",
        )

    file.assert_called_once_with(path, encoding="utf-8")
    assert basic_config.call_args.kwargs["handlers"] == [
        stream_handler,
        file_handler,
        syslog_handler,
    ]
    stream_handler.setFormatter.assert_called_once_with(mock.ANY)
    file_handler.setFormatter.assert_not_called()
    syslog_handler.setFormatter.assert_not_called()


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
