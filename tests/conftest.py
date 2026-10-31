from dataclasses import replace

import pytest

from app.api import routes
from app import main
from app.storage import MemoryStore


@pytest.fixture(autouse=True)
def isolated_application(monkeypatch):
    memory = MemoryStore(86400, 1000)
    monkeypatch.setattr(routes, "store", memory)
    monkeypatch.setattr(main, "store", memory)
    monkeypatch.setattr(routes, "settings", replace(routes.settings, environment="development", webhook_token=""))
