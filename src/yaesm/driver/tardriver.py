"""Tar archive driver."""

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, GlobalSettings, capability
from yaesm.errors import YaesmValueError
from yaesm.representation import DataProperty, PathTree, UncompressedStream
from yaesm.ssh import SSHTarget


class TarStream(UncompressedStream):
    """An uncompressed POSIX pax archive stream."""

    suffix = ".tar"


class TarDriver(DriverBase):
    """Create portable tar archive streams."""

    def __init__(
        self,
        one_file_system: bool = True,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        if not isinstance(one_file_system, bool):
            raise YaesmValueError("one_file_system must be a boolean")
        self.one_file_system = one_file_system

    @classmethod
    def name(cls) -> str:
        return "tar"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def one_file_system(value: object) -> bool:
            if not isinstance(value, bool):
                raise vlp.Invalid("one_file_system must be a boolean")
            return value

        return vlp.Schema({vlp.Optional("one_file_system", default=True): one_file_system})

    @capability("export", adds=(DataProperty.ARCHIVED,))
    def cap_export(self, source: PathTree, base: PathTree | None = None) -> TarStream:
        excludes = tuple(self._exclude_pattern(path) for path in source.excluded_paths)
        return TarStream(
            (
                CommandStage(
                    (
                        "tar",
                        "-c",
                        "-f",
                        "-",
                        "--format=pax",
                        "--acls",
                        "--xattrs",
                        "--numeric-owner",
                        *(("--one-file-system",) if self.one_file_system else ()),
                        *(f"--exclude={exclude}" for exclude in excludes),
                        "-C",
                        source.path,
                        ".",
                    ),
                    source.ssh,
                ),
            ),
            suffixes=(TarStream.suffix,),
        )

    @staticmethod
    def _exclude_pattern(path: ty.Path) -> str:
        pattern = "".join(
            f"\\{character}" if character in "\\*?[]" else character for character in str(path)
        )
        return f"./{pattern}"
