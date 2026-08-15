"""tests/test_yaesm/test_main.py."""

import yaesm.main
from yaesm.subcommand.checksubcommand import CheckSubcommand


def test_log_file_flag_writes_to_file(path_generator, valid_config_file_generator):
    config = valid_config_file_generator()
    logfile = path_generator("yaesm_main_test.log")
    argv = ["--config", str(config), "--log-file", str(logfile), "backup", "no-such-backup"]
    assert yaesm.main.main(argv) == 1
    assert logfile.is_file()
    assert "backup not found: no-such-backup" in logfile.read_text()


def test_config_error_writes_to_log_file(path_generator):
    config = path_generator("missing-config.yml")
    logfile = path_generator("yaesm_config_error.log")
    argv = ["--config", str(config), "--log-file", str(logfile), "check"]

    assert yaesm.main.main(argv) == 1
    assert "config file does not exist" in logfile.read_text()


def test_unexpected_error_writes_traceback_to_log_file(
    monkeypatch, path_generator, valid_config_file_generator
):
    config = valid_config_file_generator()
    logfile = path_generator("yaesm_unexpected_error.log")

    def raise_unexpected_error(*_args):
        raise RuntimeError("unexpected test error")

    monkeypatch.setattr(CheckSubcommand, "main", raise_unexpected_error)
    argv = ["--config", str(config), "--log-file", str(logfile), "check"]

    assert yaesm.main.main(argv) == 1
    log = logfile.read_text()
    assert "unexpected test error" in log
    assert "Traceback" in log
