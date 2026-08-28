"""Tests for yaesm.main."""

import argparse
import logging
import os
from pathlib import Path

import pytest

import yaesm.main as main_module
from yaesm.config import Config, ConfigError
from yaesm.errors import YaesmError
from yaesm.subcommand.backupsubcommand import BackupSubcommand
from yaesm.subcommand.checksubcommand import CheckSubcommand
from yaesm.subcommand.subcommandbase import SubcommandBase


@pytest.fixture(autouse=True)
def disable_logging_configuration(monkeypatch):
    monkeypatch.setattr(main_module, "configure_logging", lambda *_args, **_kwargs: None)


def test_subcommands_are_discovered():
    assert tuple(subcommand.name() for subcommand in main_module._subcommands()) == (
        "backup",
        "check",
        "find",
        "run",
    )


def test_argument_parser_shows_only_visible_subcommands(monkeypatch):
    class VisibleSubcommand(SubcommandBase):
        """Visible command."""

        def main(self, config: Config, arguments: argparse.Namespace) -> int:
            del config, arguments
            return 0

        @classmethod
        def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument("--value")

    class HiddenSubcommand(VisibleSubcommand):
        """Hidden command."""

        hidden = True

    monkeypatch.setattr(main_module.importlib.metadata, "version", lambda _name: "1.2.3")
    parser = main_module._argument_parser((VisibleSubcommand, HiddenSubcommand))

    assert "visible" in parser.format_help()
    assert "hidden" not in parser.format_help()
    assert parser.parse_args(["hidden"]).subcommand_type is HiddenSubcommand


def test_main_loads_config_and_dispatches(tmp_path, monkeypatch):
    config = Config({"setting": True}, {})
    received = {}
    configured = []

    def parse(path):
        received["path"] = path
        return config

    def run(_self, parsed_config, arguments):
        received["config"] = parsed_config
        received["arguments"] = arguments
        return 23

    monkeypatch.setattr(main_module, "parse_config", parse)
    monkeypatch.setattr(
        main_module, "configure_logging", lambda *args, **kwargs: configured.append((args, kwargs))
    )
    monkeypatch.setattr(CheckSubcommand, "main", run)
    path = tmp_path / "config.yaml"

    status = main_module.main(["--config", str(path), "--log-level", "DEBUG", "check", "--quiet"])

    assert status == 23
    assert configured == [
        (
            ("DEBUG",),
            {"stderr": False, "logfile": None, "syslog_address": None},
        )
    ]
    assert received["path"] == path
    assert received["config"] is config
    assert received["arguments"].quiet


def test_main_configures_logging_destinations(monkeypatch):
    configured = []
    monkeypatch.setattr(
        main_module, "configure_logging", lambda *args, **kwargs: configured.append((args, kwargs))
    )
    monkeypatch.setattr(BackupSubcommand, "main", lambda *_arguments: 0)

    assert (
        main_module.main(
            [
                "--log-stderr",
                "--log-file",
                "/tmp/yaesm.log",
                "--log-syslog=/var/run/log",
                "backup",
                "home",
            ]
        )
        == 0
    )
    assert configured == [
        (
            ("INFO",),
            {
                "stderr": True,
                "logfile": Path("/tmp/yaesm.log"),
                "syslog_address": "/var/run/log",
            },
        )
    ]


def test_main_uses_default_config_path(monkeypatch):
    paths = []
    monkeypatch.setattr(
        main_module,
        "parse_config",
        lambda path: paths.append(path) or Config({}, {}),
    )
    monkeypatch.setattr(CheckSubcommand, "main", lambda *_arguments: 0)

    assert main_module.main(["check"]) == 0
    assert paths == [Path("/etc/yaesm/config.yaml")]


def test_main_skips_config_for_subcommand_that_does_not_require_it(monkeypatch):
    received = []

    def run(_self, config, _arguments):
        received.append(config)
        return 0

    monkeypatch.setattr(
        main_module,
        "parse_config",
        lambda _path: pytest.fail("configuration should not be parsed"),
    )
    monkeypatch.setattr(BackupSubcommand, "main", run)

    assert main_module.main(["backup", "home"]) == 0
    assert received == [Config({}, {})]


def test_main_uses_sys_argv_when_argv_is_omitted(monkeypatch):
    monkeypatch.setattr(main_module.sys, "argv", ["yaesm", "check", "--quiet"])
    monkeypatch.setattr(main_module, "parse_config", lambda _path: Config({}, {}))
    monkeypatch.setattr(CheckSubcommand, "main", lambda _self, _config, args: int(args.quiet))

    assert main_module.main() == 1


def test_main_reports_configuration_errors(caplog, monkeypatch):
    def fail(_path):
        raise ConfigError(("first error", "second error"))

    monkeypatch.setattr(main_module, "parse_config", fail)

    with caplog.at_level(logging.ERROR):
        assert main_module.main(["check"]) == os.EX_CONFIG

    assert "configuration error: configuration errors:" in caplog.text
    assert "  - first error" in caplog.text
    assert "  - second error" in caplog.text
    assert caplog.records[-1].exc_info is None


def test_main_reports_expected_errors_without_traceback(caplog, monkeypatch):
    monkeypatch.setattr(main_module, "parse_config", lambda _path: Config({}, {}))

    def fail(*_arguments):
        raise YaesmError("expected failure")

    monkeypatch.setattr(CheckSubcommand, "main", fail)

    with caplog.at_level(logging.ERROR):
        assert main_module.main(["check"]) == 1

    assert caplog.records[-1].message == "expected failure"
    assert caplog.records[-1].exc_info is None


def test_main_reports_unexpected_errors_with_traceback(caplog, monkeypatch):
    monkeypatch.setattr(main_module, "parse_config", lambda _path: Config({}, {}))

    def fail(*_arguments):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(CheckSubcommand, "main", fail)

    with caplog.at_level(logging.ERROR):
        assert main_module.main(["check"]) == 1

    assert caplog.records[-1].message == "unexpected error: unexpected failure"
    assert caplog.records[-1].exc_info is not None


def test_version(capsys, monkeypatch):
    monkeypatch.setattr(main_module.importlib.metadata, "version", lambda _name: "1.2.3")

    with pytest.raises(SystemExit) as error:
        main_module.main(["--version"])

    assert error.value.code == 0
    assert capsys.readouterr().out == "yaesm 1.2.3\n"


@pytest.mark.parametrize("arguments", [[], ["unknown"]])
def test_invalid_command_exits_with_usage_error(arguments):
    with pytest.raises(SystemExit) as error:
        main_module.main(arguments)

    assert error.value.code == 2


def test_argument_parser_defaults():
    arguments = main_module._argument_parser((CheckSubcommand,)).parse_args(["check"])

    assert arguments.config == Path("/etc/yaesm/config.yaml")
    assert arguments.log_level == "INFO"
    assert arguments.log_syslog is None
    assert not arguments.log_stderr
    assert arguments.log_file is None
    assert arguments.subcommand_type is CheckSubcommand


def test_log_level_choices():
    parser = main_module._argument_parser((CheckSubcommand,))

    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        name = logging.getLevelName(level)
        assert parser.parse_args(["--log-level", name, "check"]).log_level == name


@pytest.mark.parametrize(
    ("options", "address"),
    [
        (["--log-syslog", "--log-level", "INFO"], "/dev/log"),
        (["--log-syslog=/var/run/log"], "/var/run/log"),
    ],
)
def test_syslog_address(options, address):
    arguments = main_module._argument_parser((CheckSubcommand,)).parse_args([*options, "check"])

    assert arguments.log_syslog == address


def test_subcommands_are_subcommand_classes():
    assert all(issubclass(subcommand, SubcommandBase) for subcommand in main_module._subcommands())
