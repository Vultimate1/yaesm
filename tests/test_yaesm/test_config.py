"""tests/test_yaesm/test_config.py"""
# import pytest

from yaesm.config import parse_file

def test_parse_file():
    """Tests on a valid YAML config file."""
    parse_file("tests/test_config.yaml")
    assert True
