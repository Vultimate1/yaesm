"""Zstandard compression driver."""

import voluptuous as vlp

from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, GlobalSettings
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, CompressedStream
from yaesm.ssh import SSHTarget


class ZstdStream(CompressedStream):
    """A Zstandard-compressed byte stream."""

    suffix = ".zst"


class ZstdDriver(DriverBase):
    """Compress byte streams using Zstandard."""

    def __init__(
        self,
        level: int = 3,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 19:
            raise YaesmValueError(f"level must be an integer from 1 to 19, got {level!r}")
        self.level = level

    @classmethod
    def name(cls) -> str:
        return "zstd"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def level(value: object) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 19:
                raise vlp.Invalid("level must be an integer from 1 to 19")
            return value

        mapping = vlp.Schema({vlp.Optional("level", default=3): level})
        return vlp.Schema(
            lambda value: mapping({"level": value} if isinstance(value, int) else value)
        )

    def cap_compress(self, source: CommandStream) -> ZstdStream:
        return ZstdStream(
            (
                *source.stages,
                CommandStage(
                    (
                        "zstd",
                        "--compress",
                        "--stdout",
                        "--quiet",
                        f"-{self.level}",
                    ),
                    self.ssh,
                ),
            ),
            suffixes=(*source.suffixes, ZstdStream.suffix),
        )
