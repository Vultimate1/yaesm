"""Tests for yaesm.subcommand.findsubcommand."""

import argparse
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.config import BackupGroup, BackupTargetError, Config
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.query import FindQuery, FindQueryError
from yaesm.representation import Representation
from yaesm.schedule import CronSchedule, Schedule
from yaesm.subcommand import findsubcommand
from yaesm.subcommand.findsubcommand import FindError, FindSubcommand
from yaesm.subcommand.subcommandbase import TargetSelection, TargetSelectionMode


def find_config(backups: dict[str, Backup]) -> Config:
    return Config({"scheduler": {"timezone": ZoneInfo("UTC")}}, backups)


def arguments(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    FindSubcommand.configure_argparser(parser)
    return parser.parse_args(values)


def artifact(
    hour: int,
    minute: int = 0,
    schedule: str = "hourly",
    backup_name: str = "home",
) -> BackupArtifact:
    return BackupArtifact(
        BackupOperation(
            backup_name,
            schedule,
            datetime(2026, 8, 20, hour, minute),
        ),
        Representation(),
    )


def configured_backup(name: str, artifacts: tuple[BackupArtifact, ...]) -> tuple[Backup, mock.Mock]:
    destination = mock.Mock()
    destination.cap_list.return_value = artifacts
    destination.format_locator.side_effect = lambda item: f"locator:{item.name}"
    backup = Backup(name, mock.Mock(), destination)
    return backup, destination


def test_find_errors_are_expected_value_errors():
    assert all(
        issubclass(FindError, parent) for parent in (YaesmError, YaesmValueError, ValueError)
    )


def test_find_reexports_canonical_query_error():
    with pytest.raises(findsubcommand.FindQueryError):
        FindQuery(["after"])


def test_find_arguments_with_positional_query():
    parsed = arguments("home", "after", "2026-01-01")

    assert FindSubcommand.target_selection is TargetSelectionMode.REQUIRED
    assert parsed.targets == TargetSelection(("home",))
    assert parsed.query == ["after", "2026-01-01"]
    assert parsed.additional_queries == []


def test_find_arguments_with_additional_queries():
    parsed = arguments(
        "home",
        "newest",
        "--query",
        "oldest",
        "--query",
        "closest",
        "12:30",
    )

    assert parsed.query == ["newest"]
    assert parsed.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_find_arguments_with_only_optional_queries():
    parsed = arguments("home", "-q", "oldest", "-q", "closest", "12:30")

    assert parsed.query == []
    assert parsed.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_find_argument_defaults():
    parsed = arguments("home")

    assert parsed.query == []
    assert parsed.additional_queries == []
    assert parsed.schedules == []
    assert parsed.null is False


def test_find_null_argument():
    assert arguments("home", "-0").null is True


def test_find_arguments_normalize_names_and_schedules():
    parsed = arguments(
        "home,,root, home, ,root",
        "--schedules",
        "hourly,,daily,hourly",
        "--schedule",
        "weekly",
    )

    assert parsed.targets == TargetSelection(("home", "root"))
    assert parsed.schedules == ["hourly", "daily", "weekly"]


def test_find_defaults_to_all_and_formats_locators(capsys):
    artifacts = (artifact(12), artifact(10, schedule="daily"))
    backup, destination = configured_backup("home", artifacts)

    assert FindSubcommand().main(find_config({"home": backup}), arguments("home")) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[0].name}",
        f"locator:{artifacts[1].name}",
    ]
    destination.cap_list.assert_called_once_with("home")
    assert destination.format_locator.call_args_list == [
        mock.call(artifacts[0]),
        mock.call(artifacts[1]),
    ]


def test_find_can_separate_locators_with_null_bytes(capsys):
    artifacts = (artifact(12), artifact(10))
    backup, _destination = configured_backup("home", artifacts)

    assert FindSubcommand().main(find_config({"home": backup}), arguments("home", "-0")) == 0

    assert capsys.readouterr().out == (
        f"locator:{artifacts[0].name}\0locator:{artifacts[1].name}\0"
    )


def test_find_includes_artifacts_under_previous_names(capsys):
    stored = artifact(12, schedule="daily", backup_name="old-home")
    destination = mock.Mock()
    destination.cap_list.side_effect = lambda name: (stored,) if name == "old-home" else ()
    destination.format_locator.return_value = "/backups/old-artifact"
    backup = Backup(
        "home",
        mock.Mock(),
        destination,
        schedules=(
            Schedule(
                "nightly",
                CronSchedule("0 1 * * *"),
                previous_names=("daily",),
            ),
        ),
        previous_names=("old-home",),
    )

    assert (
        FindSubcommand().main(
            find_config({"home": backup}),
            arguments("home,old-home", "--schedule", "daily"),
        )
        == 0
    )

    assert capsys.readouterr().out == "/backups/old-artifact\n"
    assert destination.cap_list.call_args_list == [mock.call("home"), mock.call("old-home")]
    found = destination.format_locator.call_args.args[0]
    assert found.operation.backup_name == "home"
    assert found.operation.schedule_name == "nightly"
    assert found.representation is stored.representation


def test_find_combines_queries_without_duplicates(capsys):
    artifacts = (artifact(12), artifact(11), artifact(10))
    backup, _destination = configured_backup("home", artifacts)
    parsed = arguments(
        "home",
        "newest",
        "--query",
        "after",
        "2026-08-20T10:00",
    )

    assert FindSubcommand().main(find_config({"home": backup}), parsed) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[0].name}",
        f"locator:{artifacts[1].name}",
    ]


def test_find_uses_configured_timezone(capsys):
    artifacts = (artifact(16), artifact(15))
    backup, _destination = configured_backup("home", artifacts)
    config = Config(
        {"scheduler": {"timezone": ZoneInfo("America/New_York")}},
        {"home": backup},
    )

    FindSubcommand().main(config, arguments("home", "after", "2026-08-20T11:30"))

    assert capsys.readouterr().out.splitlines() == [f"locator:{artifacts[0].name}"]


def test_optional_queries_do_not_implicitly_add_all(capsys):
    artifacts = (artifact(12), artifact(11), artifact(10))
    backup, _destination = configured_backup("home", artifacts)
    parsed = arguments(
        "home",
        "--query",
        "oldest",
        "--query",
        "closest",
        "2026-08-20T11:00",
    )

    assert FindSubcommand().main(find_config({"home": backup}), parsed) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[1].name}",
        f"locator:{artifacts[2].name}",
    ]


def test_find_supports_multiple_backup_names_in_requested_order(capsys):
    home_artifact = artifact(12, backup_name="home")
    root_artifact = artifact(11, backup_name="root")
    home, _ = configured_backup("home", (home_artifact,))
    root, _ = configured_backup("root", (root_artifact,))
    config = Config(
        {"scheduler": {"timezone": ZoneInfo("UTC")}},
        {"root": root, "home": home},
        {"selected": BackupGroup("selected", ("home", "root"))},
    )

    assert FindSubcommand().main(config, arguments("selected,home")) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{home_artifact.name}",
        f"locator:{root_artifact.name}",
    ]


def test_find_filters_schedules(capsys):
    hourly = artifact(12)
    daily = artifact(11, schedule="daily")
    weekly = artifact(10, schedule="weekly")
    backup, _destination = configured_backup("home", (hourly, daily, weekly))

    assert (
        FindSubcommand().main(
            find_config({"home": backup}),
            arguments("home", "--schedules", "daily,weekly"),
        )
        == 0
    )

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{daily.name}",
        f"locator:{weekly.name}",
    ]


def test_find_returns_success_without_matches(capsys):
    backup, _destination = configured_backup("home", (artifact(12),))

    assert (
        FindSubcommand().main(
            find_config({"home": backup}),
            arguments("home", "after", "2026-08-21"),
        )
        == 0
    )
    assert capsys.readouterr().out == ""


def test_find_rejects_unknown_backup_target():
    with pytest.raises(BackupTargetError, match="unknown backup target: 'missing'"):
        FindSubcommand().main(find_config({}), arguments("missing"))


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("after",), "invalid query"),
        (("after", "not-a-time"), "invalid time: not-a-time"),
    ],
)
def test_find_rejects_invalid_query_before_listing(tokens, message):
    backup, destination = configured_backup("home", ())

    with pytest.raises(FindError, match="cannot search backups") as caught:
        FindSubcommand().main(
            find_config({"home": backup}),
            arguments("home", *tokens),
        )

    assert isinstance(caught.value.__cause__, FindQueryError)
    assert message in str(caught.value.__cause__)
    assert caught.value.format() == f"cannot search backups\n  {caught.value.__cause__}"
    destination.cap_list.assert_not_called()
