def test_ruff_checks_cover_source(pytestconfig):
    assert "src" in pytestconfig.getini("testpaths")
    assert "--ruff" in pytestconfig.getini("addopts")
    assert "--ruff-format" in pytestconfig.getini("addopts")
