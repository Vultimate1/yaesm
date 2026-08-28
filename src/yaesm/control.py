"""Unix socket control protocol for a running yaesm process."""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from pathlib import Path

import yaesm.ty as ty
from yaesm.errors import YaesmError

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_SOCKET = Path("/run/yaesm/control.sock")
ControlMessage: ty.TypeAlias = dict[str, object]
ControlHandler: ty.TypeAlias = ty.Callable[
    [ty.Mapping[str, object]], ty.Iterable[ty.Mapping[str, object]]
]


class ControlError(YaesmError):
    """Raised when control socket communication fails."""


def send_request(
    path: ty.Path,
    request: ty.Mapping[str, object],
) -> ty.Iterator[ControlMessage]:
    """Send one request and yield responses through its final result."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            try:
                connection.connect(str(path))
            except (FileNotFoundError, ConnectionRefusedError) as error:
                raise ControlError(f"could not connect to the yaesm scheduler at {path}") from error
            with connection.makefile("rwb") as stream:
                stream.write((json.dumps(request) + "\n").encode())
                stream.flush()
                while data := stream.readline():
                    response = json.loads(data)
                    if not isinstance(response, dict):
                        raise ControlError("control response must be an object")
                    yield response
                    if response.get("type") == "result":
                        return
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ControlError(f"control request failed: {error}") from error
    raise ControlError("control socket closed without a result")


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
        logger.info("control socket listening at %s", self.path)

    def stop(self) -> None:
        """Stop serving and remove the socket."""
        self.shutdown()
        self.server_close()
        self._thread.join()
        self.path.unlink(missing_ok=True)
