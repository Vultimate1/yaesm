"""GnuPG encryption driver."""

import os
from pathlib import Path

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.driver.driverbase import DriverBase, GlobalSettings
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, EncryptedStream
from yaesm.ssh import SSHTarget, command_for_ssh


class GPGStream(EncryptedStream):
    """An OpenPGP-encrypted byte stream."""

    suffix = ".gpg"


class GPGDriver(DriverBase):
    """Encrypt byte streams for a public key using GnuPG."""

    def __init__(
        self,
        public_key: ty.Path,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings)
        if not isinstance(public_key, str | Path):
            raise YaesmValueError("public_key must be a path")
        public_key = Path(public_key)
        if not public_key.is_absolute():
            raise YaesmValueError("public_key must be an absolute path")
        self.public_key = public_key
        self.ssh = ssh

    @classmethod
    def name(cls) -> str:
        return "gpg"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def public_key(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("public_key must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("public_key must be an absolute path")
            return path

        def ssh(value: object) -> SSHTarget:
            if not isinstance(value, SSHTarget):
                raise vlp.Invalid("ssh must be an SSHTarget")
            return value

        mapping = vlp.Schema(
            {
                vlp.Required("public_key"): public_key,
                vlp.Optional("ssh"): ssh,
            }
        )
        return vlp.Schema(
            lambda value: mapping({"public_key": value} if isinstance(value, str | Path) else value)
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        if role is not CheckRole.TRANSFORM:
            return ()
        return (
            self._command_check(
                f"public key can encrypt data: {self.public_key}",
                (
                    *self._command(),
                    "--output",
                    os.devnull,
                    "--encrypt",
                    str(self.public_key),
                ),
            ),
        )

    def _check_ssh(self) -> SSHTarget | None:
        return self.ssh

    def cap_encrypt(self, source: CommandStream) -> GPGStream:
        return GPGStream(
            (*source.commands, command_for_ssh(self.ssh, (*self._command(), "--encrypt"))),
            suffixes=(*source.suffixes, GPGStream.suffix),
        )

    def _command(self) -> tuple[str, ...]:
        return (
            self.executable_name(),
            "--batch",
            "--no-tty",
            "--no-keyring",
            "--compress-algo",
            "none",
            "--recipient-file",
            str(self.public_key),
        )
