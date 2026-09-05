"""The backup subcommand."""

import argparse
import sys
from pathlib import Path

from yaesm.config import Config
from yaesm.control import DEFAULT_CONTROL_SOCKET, ControlError, send_request
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


class BackupSubcommand(SubcommandBase):
    """Run a configured backup immediately."""

    target_selection = TargetSelectionMode.REQUIRED
    config_required = False

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        del config
        request = {"command": "backup", "targets": arguments.targets.names}
        if arguments.schedule is not None:
            request["schedule"] = arguments.schedule

        for response in send_request(arguments.control_socket, request):
            match response.get("type"):
                case "log":
                    print(response.get("message", ""), file=sys.stderr)
                case "result":
                    if response.get("ok") is True:
                        return 0
                    if response.get("error_logged") is True:
                        return 1
                    raise ControlError(str(response.get("error", "backup request failed")))
        raise ControlError("backup request returned no result")

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--schedule", help="on-demand schedule to use")
        parser.add_argument(
            "--control-socket",
            type=Path,
            default=DEFAULT_CONTROL_SOCKET,
            help="path to the control socket",
        )
