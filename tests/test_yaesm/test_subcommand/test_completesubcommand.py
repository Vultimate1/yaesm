from pathlib import Path

import pytest
import yaml

import yaesm.config
import yaesm.main
from yaesm.subcommand.completesubcommand import completion_candidates


@pytest.fixture
def completion_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    settings = {
        "backend": "rsync",
        "src_dir": str(tmp_path),
        "dst_dir": str(tmp_path),
        "timeframes": [],
    }
    config.write_text(
        yaml.safe_dump({name: settings.copy() for name in ["archive", "home", "photos"]}),
        encoding="utf-8",
    )
    return config


def _complete(
    words: list[str], current: str, config: Path = Path("/missing/config.yaml")
) -> list[str]:
    return completion_candidates(words, current, config)


def test_completes_visible_subcommands_and_global_options():
    candidates = _complete([], "")

    assert {"backup", "check", "find", "run", "--config", "--log-level"} <= set(candidates)
    assert "__complete" not in candidates


def test_completes_global_option():
    assert _complete([], "--log-l") == ["--log-level"]


def test_completes_log_level_case_insensitively():
    assert _complete(["--log-level"], "d") == ["DEBUG"]
    assert _complete([], "--log-level=wa") == ["--log-level=WARNING"]


@pytest.mark.parametrize("subcommand", ["backup", "check", "find"])
def test_completes_configured_backup_names(completion_config: Path, subcommand: str):
    words = ["--config", str(completion_config), subcommand]

    assert _complete(words, "h") == ["home"]


def test_completes_comma_separated_backup_names(completion_config: Path):
    words = ["--config", str(completion_config), "backup"]

    assert _complete(words, "archive,p") == ["archive,photos"]


def test_does_not_repeat_comma_separated_backup_names(completion_config: Path):
    words = ["--config", str(completion_config), "backup"]

    assert _complete(words, "archive,a") == []


def test_normalizes_comma_separated_backup_names(completion_config: Path):
    words = ["--config", str(completion_config), "backup"]

    assert _complete(words, "archive,,p") == ["archive,photos"]


def test_completes_attached_config_option(completion_config: Path):
    words = [f"--config={completion_config}", "backup"]

    assert _complete(words, "ph") == ["photos"]


def test_completes_find_query(completion_config: Path):
    words = ["--config", str(completion_config), "find", "home"]

    assert _complete(words, "cl") == ["closest"]


def test_find_query_completion_does_not_parse_config(monkeypatch: pytest.MonkeyPatch):
    def unexpected_parse_config(*_args):
        pytest.fail("config should not be parsed after the backup name")

    monkeypatch.setattr(yaesm.config, "parse_config", unexpected_parse_config)

    assert _complete(["find", "home"], "cl") == ["closest"]


def test_completes_additional_find_query(completion_config: Path):
    words = ["--config", str(completion_config), "find", "home", "--query"]

    assert _complete(words, "old") == ["oldest"]


def test_completes_comma_separated_timeframes(completion_config: Path):
    words = ["--config", str(completion_config), "find", "home", "--timeframe"]

    assert _complete(words, "daily,w") == ["daily,weekly"]


def test_completes_timeframe_attached_to_option(completion_config: Path):
    words = ["--config", str(completion_config), "find", "home"]

    assert _complete(words, "--timeframe=da") == ["--timeframe=daily"]


def test_does_not_repeat_comma_separated_timeframes(completion_config: Path):
    words = ["--config", str(completion_config), "find", "home", "--timeframe"]

    assert _complete(words, "daily,d") == []


def test_completes_subcommand_option_after_positional(completion_config: Path):
    words = ["--config", str(completion_config), "backup", "home"]

    assert _complete(words, "--k") == ["--keep"]


def test_does_not_offer_candidates_for_keep_value():
    assert _complete(["backup", "--keep"], "") == []


def test_completes_paths(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.touch()

    assert _complete(["--config"], str(config)[:-4]) == [str(config)]


def test_hidden_completion_subcommand_is_not_in_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        yaesm.main.main(["--help"])

    assert exc_info.value.code == 0
    assert "__complete" not in capsys.readouterr().out


def test_hidden_completion_subcommand_does_not_require_config(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    missing_config = tmp_path / "missing.yaml"

    assert (
        yaesm.main.main(
            [
                "--config",
                str(missing_config),
                "__complete",
                "--current=b",
                "--",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "backup\n"
