"""Zstandard compression driver."""

import voluptuous as vlp

from yaesm.check import Check, CheckRole
from yaesm.driver.driverbase import DriverBase
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, CompressedStream


class ZstdStream(CompressedStream):
    """A Zstandard-compressed byte stream."""


class ZstdDriver(DriverBase):
    """Compress byte streams using Zstandard."""

    def __init__(self, level: int = 3) -> None:
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

        return vlp.Schema({vlp.Optional("level", default=3): level})

    def check(self, role: CheckRole) -> tuple[Check, ...]:
        return ()

    def cap_compress(self, source: CommandStream) -> ZstdStream:
        return ZstdStream(
            (
                *source.commands,
                (
                    "zstd",
                    "--compress",
                    "--stdout",
                    "--quiet",
                    f"-{self.level}",
                ),
            )
        )
