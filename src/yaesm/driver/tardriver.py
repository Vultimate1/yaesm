"""Tar archive driver."""

import contextlib
import dataclasses
from pathlib import Path

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings, capability
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, PathTree, UncompressedStream
from yaesm.ssh import SSHTarget, command_for_ssh


class TarStream(UncompressedStream):
    """An uncompressed POSIX pax archive stream."""

    suffix = ".tar"


@dataclasses.dataclass(frozen=True)
class TarTree(PathTree):
    """An extracted archive inside a private temporary directory."""

    temporary_directory: ty.Path = dataclasses.field(kw_only=True)


class TarDriver(DriverBase):
    """Create portable tar archive streams."""

    def __init__(
        self,
        one_file_system: bool = True,
        ssh: SSHTarget | None = None,
        *,
        tmpdir: str | ty.Path | None = None,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        if not isinstance(one_file_system, bool):
            raise YaesmValueError("one_file_system must be a boolean")
        self.one_file_system = one_file_system
        if tmpdir is not None and not isinstance(tmpdir, str | Path):
            raise YaesmValueError("tmpdir must be a path or null")
        self.tmpdir = None if tmpdir is None else Path(tmpdir)
        if self.tmpdir is not None and not self.tmpdir.is_absolute():
            raise YaesmValueError("tmpdir must be an absolute path")

    @classmethod
    def name(cls) -> str:
        return "tar"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def one_file_system(value: object) -> bool:
            if not isinstance(value, bool):
                raise vlp.Invalid("one_file_system must be a boolean")
            return value

        def tmpdir(value: object) -> ty.Path | None:
            if value is None:
                return None
            if not isinstance(value, str | Path):
                raise vlp.Invalid("tmpdir must be a path or null")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("tmpdir must be an absolute path")
            return path

        return vlp.Schema(
            {
                vlp.Optional("one_file_system", default=True): one_file_system,
                vlp.Optional("tmpdir"): tmpdir,
            }
        )

    def cap_cleanup(self, representation: TarTree) -> None:
        """Remove a temporary extraction directory."""
        # Repair each directory before descending, without following archive symlinks.
        self.runner.run(
            command_for_ssh(
                representation.ssh,
                (
                    "find",
                    "-P",
                    representation.temporary_directory,
                    "-type",
                    "d",
                    "-exec",
                    "chmod",
                    "u+rwx",
                    "--",
                    "{}",
                    ";",
                ),
            )
        )
        self.runner.run(
            command_for_ssh(
                representation.ssh,
                ("rm", "-rf", "--", representation.temporary_directory),
            )
        )

    @capability("detransform", pipeline=False, temporary=True)
    def cap_detransform(self, source: CommandStream) -> TarTree:
        self._validate_suffix(source, TarStream.suffix)

        command: list[str | ty.Path] = ["mktemp", "-d"]
        if self.tmpdir is not None:
            command.append(self.tmpdir / ".yaesm-tar.XXXXXX")

        result = self.runner.run(
            command_for_ssh(self.ssh, command),
            capture_output=True,
        )
        tmpdir = (result.stdout or "").removesuffix("\n")
        if not tmpdir:
            raise DriverError("mktemp did not return a temporary directory")
        temporary_directory = Path(tmpdir)
        tree = TarTree(
            temporary_directory / "data",
            self.ssh,
            temporary_directory=temporary_directory,
        )

        try:
            self.runner.run(command_for_ssh(tree.ssh, ("mkdir", "--", tree.path)))
            self.runner.pipeline(
                (
                    *source.stages,
                    CommandStage(
                        (
                            "tar",
                            "-x",
                            "-f",
                            "-",
                            "--acls",
                            "--xattrs",
                            "--numeric-owner",
                            "-C",
                            tree.path,
                        ),
                        tree.ssh,
                    ),
                )
            )
        except BaseException:
            with contextlib.suppress(Exception):
                self.cap_cleanup(tree)
            raise

        return tree

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
