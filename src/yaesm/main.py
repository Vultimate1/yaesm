"""Yaesm command-line entry point."""

import argparse
import importlib.metadata
import inspect
import logging
import os
import sys
from pathlib import Path

from yaesm.config import Config, ConfigError, parse_config
from yaesm.errors import YaesmError
from yaesm.logging import configure as configure_logging
from yaesm.subcommand import load_subcommands
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


def _subcommands() -> tuple[type[SubcommandBase], ...]:
    load_subcommands()
    return tuple(
        sorted(
            (
                subcommand
                for subcommand in SubcommandBase.__subclasses__()
                if subcommand.__module__.startswith("yaesm.subcommand.")
                and not inspect.isabstract(subcommand)
            ),
            key=lambda subcommand: subcommand.name(),
        )
    )


def _argument_parser(
    subcommands: tuple[type[SubcommandBase], ...],
) -> argparse.ArgumentParser:
    visible = tuple(subcommand.name() for subcommand in subcommands if not subcommand.hidden)
    parser = argparse.ArgumentParser(
        prog="yaesm",
        description="A backup tool with support for multiple filesystems.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('yaesm')}",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("/etc/yaesm/config.yaml"),
        help="path to the configuration file",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
        help="logging level",
    )
    parser.add_argument(
        "--log-syslog",
        nargs="?",
        const="/dev/log",
        metavar="ADDRESS",
        help="log to syslog at ADDRESS (default: /dev/log)",
    )
    parser.add_argument("--log-stderr", action="store_true", help="log to standard error")
    parser.add_argument("--log-file", type=Path, metavar="FILE", help="append logs to FILE")
    parsers = parser.add_subparsers(
        title="subcommands",
        dest="subcommand",
        required=True,
        metavar="{" + ",".join(visible) + "}",
    )
    for subcommand in subcommands:
        if subcommand.hidden:
            subparser = parsers.add_parser(subcommand.name(), description=subcommand.description())
        else:
            subparser = parsers.add_parser(
                subcommand.name(),
                description=subcommand.description(),
                help=subcommand.description(),
            )
        subcommand.add_argparser_arguments(subparser)
        subparser.set_defaults(subcommand_type=subcommand)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run yaesm and return its exit status."""
    arguments = _argument_parser(_subcommands()).parse_args(argv)
    configure_logging(
        arguments.log_level,
        stderr=arguments.log_stderr,
        message_only_stderr=arguments.subcommand != "run",
        logfile=arguments.log_file,
        syslog_address=arguments.log_syslog,
    )
    subcommand: type[SubcommandBase] = arguments.subcommand_type

    try:
        config = parse_config(arguments.config) if subcommand.config_required else Config({}, {})
        return subcommand().main(config, arguments)
    except ConfigError as error:
        logger.error("configuration error: %s", error.format())
        return os.EX_CONFIG
    except YaesmError as error:
        logger.error("%s", error.format())
        return 1
    except Exception as error:
        logger.exception("unexpected error: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
