"""Shared base exception for expected yaesm failures."""


class YaesmError(Exception):
    """Base class for expected yaesm failures."""

    def format(self) -> str:
        """Format this error and any expected cause."""
        lines = str(self).splitlines()
        if isinstance(self.__cause__, YaesmError):
            lines.extend(f"  {line}" for line in self.__cause__.format().splitlines())
        return "\n".join(lines)
