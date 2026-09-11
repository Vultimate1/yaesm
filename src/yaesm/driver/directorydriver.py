"""Plain directory source driver."""

from pathlib import Path

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.driver.driverbase import DriverBase, GlobalSettings
from yaesm.representation import PathTree
from yaesm.ssh import SSHTarget


class DirectoryDriver(DriverBase):
    """Make a local or remote directory tree available to a backup pipeline."""

    def __init__(
        self,
        location: ty.Path,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        self.location = Path(location)

    @classmethod
    def name(cls) -> str:
        return "directory"

    @classmethod
    def executable_check_command(cls) -> None:
        return None

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        return vlp.Schema(lambda value: {"location": absolute_path(value)})

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        if role is not CheckRole.SOURCE:
            return ()
        return tuple(
            self._command_check(f"{description}: {self.location}", command)
            for description, command in (
                ("directory exists", ("test", "-d", self.location)),
                ("directory is readable", ("test", "-r", self.location)),
                ("directory is searchable", ("test", "-x", self.location)),
            )
        )

    def cap_source(self) -> PathTree:
        return PathTree(self.location, self.ssh)
