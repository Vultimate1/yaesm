"""Integration tests for yaesm.driver.zstddriver."""

import shutil

import pytest

from yaesm.command import CommandRunner
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.representation import CommandStream


def test_zstd_compresses_and_restores_stream():
    if shutil.which("zstd") is None:
        pytest.skip("Zstandard is not installed")
    source = CommandStream((("printf", "%s", "compressed backup content"),))
    stream = ZstdDriver().cap_compress(source)

    result = CommandRunner().pipeline(
        (
            *stream.commands,
            ("zstd", "--decompress", "--stdout", "--quiet"),
        ),
        capture_output=True,
    )

    assert result.stdout == "compressed backup content"
