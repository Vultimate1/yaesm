"""Unix socket control protocol for a running yaesm process."""

from __future__ import annotations

import json
import logging
import os
import socketserver
import threading

import yaesm.ty as ty
from yaesm.errors import YaesmError

logger = logging.getLogger(__name__)

ControlMessage: ty.TypeAlias = dict[str, object]
ControlHandler: ty.TypeAlias = ty.Callable[
    [ty.Mapping[str, object]], ty.Iterable[ty.Mapping[str, object]]
]


class ControlError(YaesmError):
    """Raised when control socket communication fails."""


class _RequestHandler(socketserver.StreamRequestHandler):
    def _send(self, message: ty.Mapping[str, object]) -> bool:
        try:
            self.wfile.write((json.dumps(message) + "\n").encode())
            self.wfile.flush()
        except OSError:
            return False
        return True

    def handle(self) -> None:
        try:
            try:
                request = json.loads(self.rfile.readline())
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ControlError(f"invalid control request: {error}") from error
            if not isinstance(request, dict):
                raise ControlError("control request must be an object")

            handler = ty.cast(ControlServer, self.server).handler
            for message in handler(request):
                if not self._send(message) or message.get("type") == "result":
                    return
        except YaesmError as error:
            self._send({"type": "result", "ok": False, "error": error.format()})
        except Exception:
            logger.exception("control request failed")
            self._send({"type": "result", "ok": False, "error": "internal control error"})


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Serve control requests over a Unix socket."""

    daemon_threads = True

    def __init__(self, path: ty.Path, handler: ControlHandler) -> None:
        self.path = path
        self.handler = handler
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and not path.is_socket():
                raise ControlError(f"control socket path is not a socket: {path}")
            path.unlink(missing_ok=True)
        except OSError as error:
            raise ControlError(f"could not open control socket {path}: {error}") from error

        try:
            super().__init__(str(path), _RequestHandler)
            os.chmod(path, 0o600)
        except OSError as error:
            self.server_close()
            path.unlink(missing_ok=True)
            raise ControlError(f"could not open control socket {path}: {error}") from error

        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self) -> None:
        """Start serving in a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and remove the socket."""
        self.shutdown()
        self.server_close()
        self._thread.join()
        self.path.unlink(missing_ok=True)
