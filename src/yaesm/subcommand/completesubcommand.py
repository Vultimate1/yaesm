import argparse
from pathlib import Path

import yaesm.config
from yaesm.backup import Backup
from yaesm.cli import parse_comma_separated
from yaesm.subcommand.findsubcommand import FindQuery
from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.timeframe import tframe_types

_GLOBAL_OPTIONS = [
    "-h",
    "--help",
    "--version",
    "-c",
    "--config",
    "--log-level",
    "--log-stderr",
    "--log-file",
    "--log-syslog",
]
_GLOBAL_VALUE_OPTIONS = {"-c", "--config", "--log-level", "--log-file", "--log-syslog"}
_SUBCOMMAND_OPTIONS = {
    "backup": ["-h", "--help", "--keep"],
    "check": ["-h", "--help", "-q", "--quiet"],
    "find": [
        "-h",
        "--help",
        "-q",
        "--query",
        "-t",
        "--timeframe",
        "--timeframes",
    ],
    "run": ["-h", "--help", "--lockfile"],
}
_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_FIND_QUERIES = [query_type.value for query_type in FindQuery.Type]
_TIMEFRAMES = tframe_types(names=True)
_PATH_OPTIONS = {"-c", "--config", "--log-file", "--lockfile"}
_TIMEFRAME_OPTIONS = ("-t", "--timeframe", "--timeframes")
_TIMEFRAME_LONG_OPTIONS = _TIMEFRAME_OPTIONS[1:]
_TIMEFRAME_ASSIGNMENTS = tuple(option + "=" for option in _TIMEFRAME_LONG_OPTIONS)
_QUERY_OPTIONS = ("-q", "--query")


def _matching(values: list[str], current: str) -> list[str]:
    return [value for value in values if value.startswith(current)]


def _comma_candidates(values: list[str], current: str) -> list[str]:
    prefix, separator, partial = current.rpartition(",")
    selected = parse_comma_separated(prefix) if separator else []
    used = set(selected)
    output_prefix = ",".join(selected) + (separator if selected else "")
    return [
        output_prefix + value
        for value in values
        if value not in used and value.startswith(partial.strip())
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


def _option_value_candidates(
    previous: str, current: str, subcommand: str | None = None
) -> list[str] | None:
    if previous == "--keep":
        return []
    if previous == "--log-level":
        return _matching(_LOG_LEVELS, current.upper())
    if previous in _PATH_OPTIONS:
        return _path_candidates(current)
    if previous == "--log-syslog":
        return _matching(["/dev/log"], current)
    if subcommand == "find" and previous in _TIMEFRAME_OPTIONS:
        return _comma_candidates(_TIMEFRAMES, current)
    if subcommand == "find" and previous in _QUERY_OPTIONS:
        return _matching(_FIND_QUERIES, current)
    return None


def _assignment_candidates(current: str, subcommand: str | None = None) -> list[str] | None:
    option, separator, partial = current.partition("=")
    if not separator:
        return None
    if option in _PATH_OPTIONS:
        values = _path_candidates(partial)
    elif option == "--log-level":
        values = _matching(_LOG_LEVELS, partial.upper())
    elif option == "--log-syslog":
        values = _matching(["/dev/log"], partial)
    elif subcommand == "find" and option in _TIMEFRAME_LONG_OPTIONS:
        values = _comma_candidates(_TIMEFRAMES, partial)
    elif subcommand == "find" and option == "--query":
        values = _matching(_FIND_QUERIES, partial)
    else:
        return None
    return [option + separator + value for value in values]


def _config_path(words: list[str], default: Path) -> Path:
    config_path = default
    for index, word in enumerate(words):
        if word in {"-c", "--config"} and index + 1 < len(words):
            config_path = Path(words[index + 1]).expanduser()
        elif word.startswith("--config="):
            config_path = Path(word.removeprefix("--config=")).expanduser()
        elif word.startswith("-c") and len(word) > 2:
            config_path = Path(word[2:]).expanduser()
    return config_path


def _backup_names(words: list[str], default_config: Path) -> list[str]:
    try:
        backups = yaesm.config.parse_config(_config_path(words, default_config))
    except yaesm.config.ConfigErrors:
        return []
    return [backup.name for backup in backups]


def _subcommand_names() -> list[str]:
    return sorted(
        cls.name()
        for cls in SubcommandBase.__subclasses__()
        if cls.__module__.startswith("yaesm.subcommand.") and not cls.hidden
    )


def _subcommand_index(words: list[str], subcommands: list[str]) -> int | None:
    skip_value = False
    for index, word in enumerate(words):
        if skip_value:
            skip_value = False
        elif word in _GLOBAL_VALUE_OPTIONS:
            skip_value = True
        elif word in subcommands:
            return index
    return None


def _has_positional(words: list[str], value_options: set[str]) -> bool:
    skip_value = False
    options_ended = False
    for word in words:
        if skip_value:
            skip_value = False
        elif not options_ended and word == "--":
            options_ended = True
        elif not options_ended and word in value_options:
            skip_value = True
        elif not options_ended and word.startswith("-"):
            continue
        else:
            return True
    return False


def _find_state(words: list[str]) -> tuple[str | None, list[str], list[str] | None]:
    backup_name = None
    positional_query = []
    additional_query = None
    iterator = iter(words)
    for word in iterator:
        if word in _TIMEFRAME_OPTIONS:
            additional_query = None
            next(iterator, None)
            continue
        if word.startswith(_TIMEFRAME_ASSIGNMENTS):
            additional_query = None
            continue
        if word in _QUERY_OPTIONS:
            additional_query = []
            continue
        if word.startswith("--query="):
            additional_query = [word.removeprefix("--query=")]
            continue
        if word.startswith("-"):
            additional_query = None
            continue
        if backup_name is None:
            backup_name = word
        elif additional_query is not None:
            additional_query.append(word)
        else:
            positional_query.append(word)
    return backup_name, positional_query, additional_query


def completion_candidates(words: list[str], current: str, default_config: Path) -> list[str]:
    """Return candidates for the partial yaesm command line."""
    subcommands = _subcommand_names()
    subcommand_index = _subcommand_index(words, subcommands)

    if subcommand_index is None:
        if words and (values := _option_value_candidates(words[-1], current)) is not None:
            return values
        if (values := _assignment_candidates(current)) is not None:
            return values
        if current.startswith("-"):
            return _matching(_GLOBAL_OPTIONS, current)
        return _matching(subcommands + _GLOBAL_OPTIONS, current)

    subcommand = words[subcommand_index]
    subcommand_words = words[subcommand_index + 1 :]
    options = _SUBCOMMAND_OPTIONS[subcommand]

    if (
        subcommand_words
        and (values := _option_value_candidates(subcommand_words[-1], current, subcommand))
        is not None
    ):
        return values
    if (values := _assignment_candidates(current, subcommand)) is not None:
        return values
    option_candidates = _matching(options, current)
    if current.startswith("-"):
        return option_candidates

    if subcommand == "run":
        return option_candidates

    if subcommand in {"backup", "check"}:
        has_positional = _has_positional(
            subcommand_words, {"--keep"} if subcommand == "backup" else set()
        )
        if not has_positional:
            return (
                _comma_candidates(_backup_names(words, default_config), current) + option_candidates
            )
        return option_candidates

    backup_name, positional_query, additional_query = _find_state(subcommand_words)
    if backup_name is None:
        return _comma_candidates(_backup_names(words, default_config), current) + option_candidates
    query = additional_query if additional_query is not None else positional_query
    query_candidates = _matching(_FIND_QUERIES, current) if not query else []
    return query_candidates + option_candidates


class __CompleteSubcommand(SubcommandBase):
    """Provide shell completion candidates."""

    hidden = True
    config_required = False

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        del backups
        words = parsed_args.words
        if words[:1] == ["--"]:
            words = words[1:]
        for candidate in completion_candidates(words, parsed_args.current, parsed_args.config):
            print(candidate)
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--current", default="")
        parser.add_argument("words", nargs=argparse.REMAINDER)
