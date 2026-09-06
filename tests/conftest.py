import pytest


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "state.json"
