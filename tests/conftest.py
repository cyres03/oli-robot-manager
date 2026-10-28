import pytest

import config
from database.connection import DatabaseConnection


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path_factory, monkeypatch):
    """Keep tests away from the real user's database, audit log and UI log."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    data_dir = home / ".oli-robot-manager"
    data_dir.mkdir()
    monkeypatch.setattr(config.APP_CONFIG, "data_dir", str(data_dir))
    # The application creates the schema at startup, so tests start from the same state.
    DatabaseConnection().initialize_schema()
