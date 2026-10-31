import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.investigation.orchestrator import InvestigationOrchestrator
from app.models import IncomingEvent
from app.storage import PostgresStore


@pytest.fixture
def database_store():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL integration tests run on EC2 using TEST_DATABASE_URL")
    schema = f"rca_test_{uuid4().hex}"
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    class IsolatedStore(PostgresStore):
        def connect(self):
            return psycopg.connect(self.database_url, connect_timeout=5, options=f"-c search_path={schema} -c statement_timeout=10000")

    isolated = IsolatedStore(database_url, 86400, 2)
    try:
        isolated.initialize()
        yield isolated
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_migration_persistence_and_deduplication(database_store):
    database_store.initialize()
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Database integration test")
    report = InvestigationOrchestrator().investigate(event)
    saved = database_store.save(event, report)
    assert database_store.ready()
    assert database_store.get(report.incident_id) == saved
    assert database_store.save(event, InvestigationOrchestrator().investigate(event)) == saved
    replacement_store = type(database_store)(database_store.database_url, 86400, 2)
    assert replacement_store.get(report.incident_id) == saved
    assert replacement_store.list(10, 0)["total"] == 1


def test_expiry_cleanup_and_size_limit(database_store):
    for index in range(3):
        event = IncomingEvent(provider="azure", event_type="test", resource_id=f"demo-{index}", summary="Retention test")
        database_store.save(event, InvestigationOrchestrator().investigate(event))
    assert database_store.list(10, 0)["total"] == 2
    expired = database_store.list(10, 0)["items"][0]["incident_id"]
    with database_store.connect() as connection:
        connection.execute("UPDATE rca_reports SET expires_at = now() - interval '1 second' WHERE incident_id = %s", (expired,))
    database_store.purge()
    assert database_store.get(expired) is None
    assert database_store.list(10, 0)["total"] == 1
