"""Tests for yaesm.ty."""

import yaesm.ty as ty


def test_all_exports_are_available():
    assert all(hasattr(ty, name) for name in ty.__all__)
