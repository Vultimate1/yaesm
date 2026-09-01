"""Tests for yaesm.subcommand.completesubcommand."""

from pathlib import Path

import pytest

import yaesm.main
from yaesm.subcommand.completesubcommand import completion_candidates


@pytest.fixture
def completion_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
pair:
  group: [home, photos]

home:
  previous_names: [old-home]
  source:
    btrfs: /source/home
  destination:
    btrfs: /destination/home
  schedules:
    daily:
      cron: "0 4 * * *"
      retention:
        keep-last: 7
    manual:
      previous_names: [adhoc]
      on-demand: {}
      retention:
        keep-all: {}

photos:
  source:
    btrfs: /source/photos
  destination:
    btrfs: /destination/photos
  schedules:
    weekly:
      cron: "0 4 * * 0"
      retention:
        keep-last: 4
    manual:
      previous_names: [emergency]
      on-demand: {}
      retention:
        keep-all: {}

root:
  source:
    btrfs: /source/root
  destination:
    btrfs: /destination/root
""".lstrip(),
        encoding="utf-8",
    )
    return config


def _complete(
    words: list[str],
    current: str,
    config: Path = Path("/missing/config.yaml"),
) -> list[str]:
    return completion_candidates(words, current, config)


def test_completes_visible_subcommands_and_parser_options():
    candidates = _complete([], "")

    assert {"backup", "check", "find", "run", "--config", "--log-level"} <= set(candidates)
    assert "__complete" not in candidates
    assert _complete(["check"], "--config-o") == ["--config-only"]
    assert _complete(["backup", "home"], "--control") == ["--control-socket"]


def test_completes_option_choices_case_insensitively():
    assert _complete(["--log-level"], "d") == ["DEBUG"]
    assert _complete([], "--log-level=wa") == ["--log-level=WARNING"]


def test_optional_global_value_does_not_consume_following_option():
    words = ["--log-syslog", "--log-level", "DEBUG", "check"]

    assert _complete(words, "--config-o") == ["--config-only"]


def test_completes_targets_groups_aliases_and_all(completion_config: Path):
    words = ["--config", str(completion_config), "backup"]

    assert _complete(words, "p") == ["pair", "photos"]
    assert _complete(words, "old") == ["old-home"]
    assert _complete(words, "@") == ["@all"]


def test_completes_comma_separated_targets_without_repeats(completion_config: Path):
    words = ["--config", str(completion_config), "backup"]

    assert _complete(words, "home,p") == ["home,pair", "home,photos"]
    assert _complete(words, "home,h") == []
    assert _complete(words, "@all,h") == []


def test_completes_only_common_on_demand_schedule_for_group(completion_config: Path):
    words = [
        "--config",
        str(completion_config),
        "backup",
        "pair",
        "--schedule",
    ]

    assert _complete(words, "m") == ["manual"]
    assert _complete(words, "d") == []


def test_completes_schedule_alias_for_single_backup(completion_config: Path):
    words = [
        f"--config={completion_config}",
        "backup",
        "home",
        "--schedule",
    ]

    assert _complete(words, "a") == ["adhoc"]


def test_completes_find_schedules_and_queries(completion_config: Path):
    words = ["--config", str(completion_config), "find", "pair"]

    assert _complete(words, "cl") == ["closest"]
    assert _complete([*words, "--query"], "old") == ["oldest"]
    assert _complete([*words, "--schedule"], "w") == ["weekly"]


def test_completes_paths(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.touch()

    assert _complete(["--config"], str(config)[:-4]) == [str(config)]


def test_hidden_completion_subcommand_is_not_in_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as error:
        yaesm.main.main(["--help"])

    assert error.value.code == 0
    assert "__complete" not in capsys.readouterr().out


def test_hidden_completion_subcommand_does_not_require_config(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    missing = tmp_path / "missing.yaml"

    assert yaesm.main.main(["--config", str(missing), "__complete", "--current=b", "--"]) == 0
    assert capsys.readouterr().out == "backup\n"
