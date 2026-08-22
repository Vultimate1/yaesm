import pytest

from yaesm.cli import parse_comma_separated


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", []),
        ("foo", ["foo"]),
        ("foo, bar,,foo,baz", ["foo", "bar", "baz"]),
    ],
)
def test_parse_comma_separated(value: str, expected: list[str]):
    assert parse_comma_separated(value) == expected
