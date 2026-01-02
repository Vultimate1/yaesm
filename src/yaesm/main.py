#!/usr/bin/env python3
import sys
import argparse
import importlib.metadata
import logging

import yaesm.logging
import yaesm.config
import yaesm.scheduler

def main(argv=None):
    """TODO"""
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    try:
        backups = yaesm.config.parse_config(args.config)
    except yaesm.config.ConfigErrors as exc:
        for err in exc.errors:
            print(f"yaesm: config error: {err}", file=sys.stderr)
        return 1

    yaesm.logging.init_logging(
        logfile=args.logfile,
        syslog=args.syslog,
        syslog_address=args.syslog,
        level=logging.DEBUG if args.verbose else logging.INFO
    )

    if args.subcommand == "run":
        return main_run(backups)

def main_run(backups):
    """TODO"""
    scheduler = yaesm.scheduler.Scheduler()
    scheduler.add_backups(backups)
    scheduler.start()

def parse_args(argv):
    """TODO"""
    parser = argparse.ArgumentParser(
        prog="yaesm",
        description="A backup tool with support for multiple filesystems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f'%(prog)s {importlib.metadata.version("yaesm")}'
    )
    parser.add_argument(
        "-c", "--config",
        default="/etc/yaesm/config.yaml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--logfile",
        metavar="PATH",
        help="Log to file at PATH"
    )
    parser.add_argument(
        "--syslog",
        action="store_true",
        help="Log to syslog"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Produce debug output"
    )
    parser.add_argument(
        "--syslog-address",
        default="/dev/log",
        help="Syslog socket address"
    )

    # subcommands
    subparsers = parser.add_subparsers(dest='subcommand', help='subcommands')

    run_parser = subparsers.add_parser(
        "run",
        help="Run yaesm scheduler (for use with init systems)"
    )
    run_parser.add_argument(
        "--pidfile",
        default="/var/run/yaesm.pid"
    )

    args = parser.parse_args(argv)
    if not args.subcommand:
        parser.error("must specify a subcommand")
    return args
