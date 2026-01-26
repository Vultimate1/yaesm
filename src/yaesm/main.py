#!/usr/bin/env python3

import sys
import argparse
import importlib.metadata
from pathlib import Path

import yaesm.logging
import yaesm.config
import yaesm.cleanup
from yaesm.subcommand.subcommandbase import SubcommandBase

def main(argv=None) -> int:
    """This is the main function of yaesm."""
    if argv is None:
        argv = sys.argv[1:]

    # yaesm.subcommand modules are loaded eagerly from their __init__.py
    subcommand_name_class_map = {cls.name(): cls for cls in SubcommandBase.__subclasses__()}

    parser = argparse.ArgumentParser(
        prog="yaesm",
        description="yaesm is a backup tool with support for multiple file systems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    subparsers = parser.add_subparsers(title="subcommands", dest="subcommand", required=True)
    for name, cls in subcommand_name_class_map.items():
        subparser = subparsers.add_parser(name)
        cls.add_argparser_arguments(subparser)
    parser.add_argument(
        "--version",
        action="version",
        version=f'%(prog)s {importlib.metadata.version("yaesm")}'
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("/etc/yaesm/config.yaml"),
        help="path to configuration file"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="set the logging level",
    )
    parser.add_argument(
        "--log-stderr",
        action="store_true",
        help="log to STDERR"
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        metavar="FILE",
        help="log to file FILE"
    )
    parser.add_argument(
        "--log-syslog",
        nargs="?",
        const=True,
        default=False,
        metavar="ADDRESS",
        help=("enable syslog logging and "
              "optionally specify syslog address "
              "(default: /dev/log)"
        )
    )
    parsed_args = parser.parse_args(argv)

    try:
        backups = yaesm.config.parse_config(parsed_args.config)
    except yaesm.config.ConfigErrors as exc:
        for err in exc.errors:
            backup, err_msg = err
            print(f"yaesm: config error: {backup}: {err_msg}", file=sys.stderr)
        return 1

    yaesm.logging.init_logging(
        level=parsed_args.log_level,
        stderr=parsed_args.log_stderr,
        syslog=bool(parsed_args.log_syslog),
        syslog_address=parsed_args.log_syslog if isinstance(parsed_args.log_syslog, str) else "/dev/log"
    )

    yaesm.cleanup.initialize_cleanup()

    try:
        exit_status = subcommand_name_class_map[parsed_args.subcommand]().main(backups, parsed_args)
        return exit_status
    except Exception as exc:
        print(f"yaesm: error: {exc}", file=sys.stderr)
        return 1
