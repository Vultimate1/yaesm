"""Tests for yaesm.logging."""

import logging
from unittest import mock

from yaesm.logging import configure


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
