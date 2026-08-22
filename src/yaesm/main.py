#!/usr/bin/env python3

import argparse
import importlib.metadata
import logging
import os
import sys
from pathlib import Path

import yaesm.config
import yaesm.ty as ty
from yaesm.cleanup import Cleanup
from yaesm.logging import configure as configure_logging
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """This is the main function of yaesm."""
    if argv is None:
        argv = sys.argv[1:]

    # yaesm.subcommand modules are loaded eagerly from the yaesm.subcommand __init__.py
    subcommand_name_class_map: dict[str, ty.Any] = {
        cls.name(): cls for cls in SubcommandBase.__subclasses__()
    }

    visible_subcommands = [
        name for name, cls in subcommand_name_class_map.items() if not cls.hidden
    ]

    parser = argparse.ArgumentParser(
        prog="yaesm",
        description="yaesm is a backup tool with support for multiple file systems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="subcommand",
        required=True,
        metavar="{" + ",".join(visible_subcommands) + "}",
    )
    for name, cls in subcommand_name_class_map.items():
        kwargs = {"description": cls.description()}
        if not cls.hidden:
            kwargs["help"] = cls.description()
        subparser = subparsers.add_parser(name, **kwargs)
        cls.add_argparser_arguments(subparser)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {importlib.metadata.version('yaesm')}"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("/etc/yaesm/config.yaml"),
        help="path to configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="set the logging level",
    )
    parser.add_argument("--log-stderr", action="store_true", help="log to STDERR")
    parser.add_argument("--log-file", type=Path, metavar="FILE", help="log to file FILE")
    parser.add_argument(
        "--log-syslog",
        nargs="?",
        const=True,
        default=False,
        metavar="ADDRESS",
        help=("enable syslog logging and optionally specify syslog address (default: /dev/log)"),
    )
    parsed_args = parser.parse_args(argv)

    configure_logging(
        level=parsed_args.log_level,
        stderr=parsed_args.log_stderr,
        logfile=parsed_args.log_file,
        syslog=bool(parsed_args.log_syslog),
        syslog_address=parsed_args.log_syslog
        if isinstance(parsed_args.log_syslog, str)
        else "/dev/log",
    )

    subcommand_class = subcommand_name_class_map[parsed_args.subcommand]
    backups = []
    if subcommand_class.config_required:
        try:
            backups = yaesm.config.parse_config(parsed_args.config)
        except yaesm.config.ConfigErrors as exc:
            for err in exc.errors:
                backup, err_msg = err
                logger.error("config error: %s: %s", backup, err_msg)
            return os.EX_CONFIG

    Cleanup.initialize()

    try:
        exit_status = subcommand_class().main(backups, parsed_args)
        return exit_status
    except Exception as exc:
        logger.exception("unexpected error: %s", exc)
        return 1
