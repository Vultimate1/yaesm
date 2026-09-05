"""Shell-completion support for yaesm's Bash, Fish, and Zsh integrations.

The hidden ``__complete`` subcommand receives the already completed command-line
words and the word currently being typed, then prints one completion candidate
per line. It derives commands and options from yaesm's argparse configuration and
adds context-dependent candidates such as configured targets, schedules, queries,
and filesystem paths. New subcommands and ordinary argparse arguments are therefore
completed automatically; only new context-sensitive value types require specialized
logic here. This is an internal interface used by the packaged shell completion
scripts, not a user-facing command.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from yaesm.config import BackupTargetError, Config, ConfigError, parse_config
from yaesm.names import ALL_TARGET_NAME
from yaesm.schedule import OnDemandSchedule
from yaesm.subcommand.findsubcommand import FindQuery
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


def _matching(values: list[str], current: str) -> list[str]:
    partial = current.casefold()
    return [value for value in values if value.casefold().startswith(partial)]


def _comma_candidates(
    values: list[str], current: str, *, exclusive: str | None = None
) -> list[str]:
    prefix, separator, partial = current.rpartition(",")
    selected = list(dict.fromkeys(filter(None, map(str.strip, prefix.split(",")))))
    if exclusive in selected:
        return []
    if selected and exclusive is not None:
        values = [value for value in values if value != exclusive]
    output_prefix = ",".join(selected) + (separator if selected else "")
    return [
        output_prefix + value
        for value in _matching(values, partial.strip())
        if value not in selected
    ]


def _path_candidates(current: str) -> list[str]:
    if current == "~":
        return ["~/"]

    expanded = Path(current).expanduser()
    if current.endswith("/"):
        directory = expanded
        partial = ""
        output_prefix = current
    else:
        directory = expanded.parent
        partial = expanded.name
        output_prefix = current[: current.rfind("/") + 1] if "/" in current else ""

    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [
        output_prefix + entry.name + ("/" if entry.is_dir() else "")
        for entry in entries
        if entry.name.startswith(partial)
        and (partial.startswith(".") or not entry.name.startswith("."))
    ]


def _option_actions(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {option: action for action in parser._actions for option in action.option_strings}


def _option_candidates(parser: argparse.ArgumentParser, current: str) -> list[str]:
    return _matching(list(_option_actions(parser)), current)


def _attached_value(
    current: str, actions: dict[str, argparse.Action]
) -> tuple[argparse.Action, str, str] | None:
    option, separator, partial = current.partition("=")
    if separator and option in actions and actions[option].nargs != 0:
        return actions[option], option + separator, partial
    for option, action in actions.items():
        if (
            len(option) == 2
            and current.startswith(option)
            and len(current) > 2
            and action.nargs != 0
        ):
            return action, option, current[2:]
    return None


@dataclass(frozen=True)
class _Scan:
    positionals: tuple[str, ...]
    value_action: argparse.Action | None = None
    value_count: int = 0


def _scan(words: list[str], parser: argparse.ArgumentParser) -> _Scan:
    actions = _option_actions(parser)
    positionals = []
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--":
            positionals.extend(words[index + 1 :])
            break
        if _attached_value(word, actions) is not None:
            index += 1
            continue
        action = actions.get(word)
        if action is None:
            if not word.startswith("-"):
                positionals.append(word)
            index += 1
            continue

        index += 1
        nargs = action.nargs
        if nargs == 0:
            continue
        if nargs is None:
            if index == len(words):
                return _Scan(tuple(positionals), action)
            index += 1
            continue
        if isinstance(nargs, int):
            available = min(nargs, len(words) - index)
            index += available
            if available < nargs:
                return _Scan(tuple(positionals), action, available)
            continue
        if nargs == "?":
            if index == len(words):
                return _Scan(tuple(positionals), action)
            if not words[index].startswith("-"):
                index += 1
            continue
        if nargs in {"*", "+"}:
            count = 0
            while index < len(words) and not words[index].startswith("-"):
                count += 1
                index += 1
            if index == len(words):
                return _Scan(tuple(positionals), action, count)
            continue
        index += 1
    return _Scan(tuple(positionals))


def _minimum_values(action: argparse.Action) -> int:
    if action.nargs in {"?", "*"}:
        return 0
    if isinstance(action.nargs, int):
        return action.nargs
    return 1


def _subcommand_context(
    parser: argparse.ArgumentParser, words: list[str]
) -> tuple[str, argparse.ArgumentParser, int] | None:
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    actions = _option_actions(parser)
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        attached = _attached_value(word, actions)
        if attached is not None:
            index += 1
            continue
        action = actions.get(word)
        if action is None:
            break
        index += 1
        if index < len(words) and (
            action.nargs is None or (action.nargs == "?" and not words[index].startswith("-"))
        ):
            index += 1
    if index >= len(words):
        return None
    name = words[index]
    subparser = subparsers.choices.get(name)
    if subparser is None:
        return None
    subcommand_type = subparser.get_default("subcommand_type")
    if subcommand_type.hidden:
        return None
    return name, subparser, index


def _config_path(parser: argparse.ArgumentParser, words: list[str], default: Path) -> Path:
    action = next(action for action in parser._actions if action.dest == "config")
    path = default
    for index, word in enumerate(words):
        if word in action.option_strings and index + 1 < len(words):
            path = Path(words[index + 1]).expanduser()
        else:
            for option in action.option_strings:
                if word.startswith(option + "="):
                    path = Path(word.removeprefix(option + "=")).expanduser()
                elif len(option) == 2 and word.startswith(option) and len(word) > 2:
                    path = Path(word[2:]).expanduser()
    return path


def _read_config(parser: argparse.ArgumentParser, words: list[str], default: Path) -> Config | None:
    try:
        return parse_config(_config_path(parser, words, default))
    except ConfigError:
        return None


def _schedule_names(config: Config, targets: str, *, on_demand: bool) -> list[str]:
    names = tuple(dict.fromkeys(filter(None, map(str.strip, targets.split(",")))))
    try:
        backups = config.backups_for_targets(*names)
    except BackupTargetError:
        return []
    schedules = [
        {
            name
            for schedule in backup.schedules
            if not on_demand or isinstance(schedule.trigger, OnDemandSchedule)
            for name in schedule.names
        }
        for backup in backups
    ]
    if not schedules:
        return []
    selected = set.intersection(*schedules) if on_demand else set.union(*schedules)
    return sorted(selected)


def completion_candidates(words: list[str], current: str, default_config: Path) -> list[str]:
    """Return candidates for an incomplete yaesm command line."""
    from yaesm.main import _argument_parser, _subcommands

    parser = _argument_parser(_subcommands())
    context = _subcommand_context(parser, words)
    config = None

    def configured() -> Config | None:
        nonlocal config
        if config is None:
            config = _read_config(parser, words, default_config)
        return config

    def value_candidates(
        action: argparse.Action,
        partial: str,
        scan: _Scan,
        subcommand: str | None = None,
    ) -> list[str]:
        if action.choices is not None:
            return _matching([str(choice) for choice in action.choices], partial)
        if action.type is Path:
            return _path_candidates(partial)
        if action.nargs == "?" and isinstance(action.const, str):
            return _matching([action.const], partial)
        if action.dest == "additional_queries" and scan.value_count == 0:
            return _matching([query.value for query in FindQuery.Type], partial)
        if action.dest in {"schedule", "schedules"}:
            value = configured()
            if value is None or not scan.positionals:
                return []
            names = _schedule_names(
                value,
                scan.positionals[0],
                on_demand=action.dest == "schedule" and subcommand == "backup",
            )
            return (
                _comma_candidates(names, partial)
                if action.dest == "schedules"
                else _matching(names, partial)
            )
        return []

    if context is None:
        scan = _scan(words, parser)
        actions = _option_actions(parser)
        if attached := _attached_value(current, actions):
            action, prefix, partial = attached
            return [prefix + value for value in value_candidates(action, partial, scan)]
        visible = [subcommand.name() for subcommand in _subcommands() if not subcommand.hidden]
        normal = (
            _option_candidates(parser, current)
            if current.startswith("-")
            else _matching(visible, current) + _option_candidates(parser, current)
        )
        if scan.value_action is None:
            return normal
        values = value_candidates(scan.value_action, current, scan)
        return values + (normal if scan.value_count >= _minimum_values(scan.value_action) else [])

    subcommand, subparser, subcommand_index = context
    subcommand_words = words[subcommand_index + 1 :]
    scan = _scan(subcommand_words, subparser)
    actions = _option_actions(subparser)
    if attached := _attached_value(current, actions):
        action, prefix, partial = attached
        return [prefix + value for value in value_candidates(action, partial, scan, subcommand)]
    options = _option_candidates(subparser, current)
    if scan.value_action is not None:
        values = value_candidates(scan.value_action, current, scan, subcommand)
        return values + (options if scan.value_count >= _minimum_values(scan.value_action) else [])
    if current.startswith("-"):
        return options

    target_action = next(
        (action for action in subparser._actions if action.dest == "targets"),
        None,
    )
    if target_action is not None and not scan.positionals:
        value = configured()
        targets = [] if value is None else sorted(value.targets_by_name)
        return _comma_candidates(targets, current, exclusive=ALL_TARGET_NAME) + options
    if subcommand == "find" and len(scan.positionals) == 1:
        return _matching([query.value for query in FindQuery.Type], current) + options
    return options


class __CompleteSubcommand(SubcommandBase):
    """Provide shell completion candidates."""

    target_selection = TargetSelectionMode.NONE
    hidden = True
    config_required = False

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        del config
        words = arguments.words
        if words[:1] == ["--"]:
            words = words[1:]
        for candidate in completion_candidates(words, arguments.current, arguments.config):
            print(candidate)
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--current", default="")
        parser.add_argument("words", nargs=argparse.REMAINDER)
