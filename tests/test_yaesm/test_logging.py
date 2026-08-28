"""Tests for yaesm.logging."""

import logging
from unittest import mock
from uuid import UUID

from yaesm.logging import RequestFilter, configure, request_id


def test_configure_uses_uniform_format():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure(logging.DEBUG)

    basic_config.assert_called_once_with(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def test_configure_defaults_to_info():
    with mock.patch.object(logging, "basicConfig") as basic_config:
        configure()

    assert basic_config.call_args.kwargs["level"] == logging.INFO


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
