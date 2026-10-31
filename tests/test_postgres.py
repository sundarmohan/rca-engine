import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.investigation.orchestrator import InvestigationOrchestrator
from app.models import IncomingEvent
from app.storage import PostgresStore, ReportConflictError, ReportTooLarge


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


def test_report_revision_preserves_expiry_event_key_and_creation(database_store):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Original report")
    report = InvestigationOrchestrator().investigate(event)
    report.metadata["report_revision"] = 99
    saved = database_store.save(event, report)
    assert saved["metadata"]["report_revision"] == 1
    with database_store.connect() as connection:
        original = connection.execute(
            "SELECT expires_at, event_key, created_at FROM rca_reports WHERE incident_id = %s", (report.incident_id,),
        ).fetchone()
    candidate = report.model_copy(update={"summary": "New evidence"})
    updated = database_store.update_report(report.incident_id, candidate, 1)
    assert updated["summary"] == "New evidence"
    assert updated["metadata"] == {**report.metadata, "report_revision": 2}
    with database_store.connect() as connection:
        unchanged = connection.execute(
            "SELECT expires_at, event_key, created_at FROM rca_reports WHERE incident_id = %s", (report.incident_id,),
        ).fetchone()
    assert unchanged == original
    assert database_store.save(event, InvestigationOrchestrator().investigate(event)) == updated
    replacement_store = type(database_store)(database_store.database_url, 86400, 2)
    assert replacement_store.get(report.incident_id) == updated
    with pytest.raises(ReportConflictError, match="^Report revision conflict$"):
        replacement_store.update_report(report.incident_id, report, 1)
    assert database_store.get(report.incident_id) == updated


@pytest.mark.parametrize("field,value", [
    ("incident_id", "different"),
    ("provider", "azure"),
    ("resource_id", "different"),
    ("resource_type", "different"),
])
def test_report_update_rejects_identity_mismatch(database_store, field, value):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Identity test")
    report = InvestigationOrchestrator().investigate(event)
    saved = database_store.save(event, report)
    candidate = report.model_copy(update={field: value})
    with pytest.raises(ValueError, match="^Report identity does not match stored incident$"):
        database_store.update_report(report.incident_id, candidate, 1)
    assert database_store.get(report.incident_id) == saved


def test_report_update_cannot_revive_expired_or_missing_report(database_store):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Expired report")
    report = InvestigationOrchestrator().investigate(event)
    assert database_store.update_report(report.incident_id, report, 1) is None
    database_store.save(event, report)
    with database_store.connect() as connection:
        connection.execute("UPDATE rca_reports SET expires_at = clock_timestamp() - interval '1 second' WHERE incident_id = %s", (report.incident_id,))
        expired = connection.execute("SELECT expires_at, event_key, report FROM rca_reports WHERE incident_id = %s", (report.incident_id,)).fetchone()
    assert database_store.update_report(report.incident_id, report, 1) is None
    with database_store.connect() as connection:
        assert connection.execute("SELECT expires_at, event_key, report FROM rca_reports WHERE incident_id = %s", (report.incident_id,)).fetchone() == expired
    assert database_store.get(report.incident_id) is None


def test_legacy_report_revision_defaults_to_one(database_store):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Legacy report")
    report = InvestigationOrchestrator().investigate(event)
    database_store.save(event, report)
    with database_store.connect() as connection:
        connection.execute("UPDATE rca_reports SET report = report #- '{metadata,report_revision}' WHERE incident_id = %s", (report.incident_id,))
    assert database_store.get(report.incident_id)["metadata"]["report_revision"] == 1
    assert database_store.list(10, 0)["items"][0]["metadata"]["report_revision"] == 1
    assert database_store.save(event, report)["metadata"]["report_revision"] == 1
    assert database_store.update_report(report.incident_id, report, 1)["metadata"]["report_revision"] == 2


def test_oversized_report_save_and_update_leave_database_unchanged(database_store):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Size limit test")
    report = InvestigationOrchestrator().investigate(event)
    saved = database_store.save(event, report)
    oversized = report.model_copy(update={"summary": "x" * (64 * 1024)})
    with pytest.raises(ReportTooLarge, match="^Report exceeds storage size limit$"):
        database_store.save(event, oversized)
    with pytest.raises(ReportTooLarge, match="^Report exceeds storage size limit$"):
        database_store.update_report(report.incident_id, oversized, 1)
    assert database_store.get(report.incident_id) == saved
    assert database_store.list(10, 0)["total"] == 1
    assert database_store.update_report(report.incident_id, report, 1)["metadata"]["report_revision"] == 2


def test_concurrent_report_updates_have_one_winner(database_store):
    event = IncomingEvent(provider="aws", event_type="test", resource_id="demo", summary="Concurrent revision test")
    report = InvestigationOrchestrator().investigate(event)
    database_store.save(event, report)
    barrier = Barrier(2)

    def update():
        barrier.wait(timeout=5)
        try:
            return database_store.update_report(report.incident_id, report, 1)
        except ReportConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: update(), range(2)))
    assert results.count("conflict") == 1
    assert database_store.get(report.incident_id)["metadata"]["report_revision"] == 2
