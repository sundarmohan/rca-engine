from dataclasses import replace

import pytest

from app.api import routes
from app import main
from app.storage import MemoryStore
from app.cloud import CloudConfiguration, CloudService


@pytest.fixture(autouse=True)
def isolated_application(monkeypatch):
    memory = MemoryStore(86400, 1000)
    monkeypatch.setattr(routes, "store", memory)
    monkeypatch.setattr(main, "store", memory)
    monkeypatch.setattr(routes, "settings", replace(routes.settings, environment="development", webhook_token="", operator_token=""))
    cloud = CloudService(CloudConfiguration())
    monkeypatch.setattr(routes, "cloud_service", cloud)
    monkeypatch.setattr(main, "cloud_service", cloud)
    yield
    cloud.close()
