"""src/yaesm/logging.py.

Logging configuration for yaesm. Call `configure()` once at startup (done in
main.py); everywhere else use the stdlib idiom `logging.getLogger(__name__)`.
"""

import logging
import logging.handlers
import shlex
import sys
from pathlib import Path

_audit_hook_installed = False


def configure(
    stderr: bool = False,
    logfile: Path | str | None = None,
    syslog: bool = False,
    syslog_address: str = "/dev/log",
    level: int | str = logging.INFO,
) -> None:
    """Configure yaesm logging. yaesm can log to any and all of stderr, syslog,
    and a file; if none are selected then stderr is used. Calling this again
    fully reconfigures logging.

    yaesm logs at DEBUG (every subprocess command, via a global audit hook),
    INFO, WARNING, and ERROR.
    """
    if not (stderr or logfile or syslog):
        stderr = True
    formatter = logging.Formatter(
        "yaesm - %(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handlers: list[logging.Handler] = []
    if syslog:
        handlers.append(logging.handlers.SysLogHandler(address=syslog_address))
    if stderr:
        handlers.append(logging.StreamHandler())
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(level)
        root_logger.addHandler(handler)
    _install_subprocess_audit_hook()


def _install_subprocess_audit_hook() -> None:
    """Install a global audit hook (once) that logs every subprocess command at
    DEBUG, giving command-level tracing without instrumenting call sites.
    """
    global _audit_hook_installed
    if _audit_hook_installed:
        return

    def hook(event: str, args: tuple) -> None:
        if event != "subprocess.Popen":
            return
        try:
            cmd = args[1]
            if isinstance(cmd, (list, tuple)):
                cmd = " ".join(shlex.quote(str(a)) for a in cmd)
            logging.getLogger("yaesm.subprocess").debug("exec: %s", cmd)
        except Exception:
            pass  # never abort the observed subprocess

    sys.addaudithook(hook)
    _audit_hook_installed = True
