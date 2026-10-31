import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import MagicMock

import psycopg
import pytest

from app.models import IncomingEvent, RCAReport
from app.retention.cache import TTLCache
from app.storage import MemoryStore, PostgresStore, ReportConflictError, ReportTooLarge, StorageUnavailable, event_key


@pytest.fixture
def report_pair():
    event = IncomingEvent(provider="aws", event_type="availability", resource_id="demo", summary="Test report")
    report = RCAReport(
        provider=event.provider,
        resource_id=event.resource_id,
        resource_type=event.resource_type,
        event_type=event.event_type,
        severity=event.severity,
        summary=event.summary,
        incident_time=event.occurred_at,
        metadata={"report_revision": 99, "investigation_mode": "deterministic"},
    )
    return event, report


@pytest.fixture
def memory_clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.retention.cache.time.monotonic", lambda: now[0])
    return now


def test_cache_replace_preserves_expiry_and_order(memory_clock):
    cache = TTLCache(10, 2)
    cache.set("first", "original")
    cache.set("second", "other")
    memory_clock[0] = 105.0
    assert cache.replace("first", "revised") is True
    assert cache.items("") == [("first", "revised"), ("second", "other")]
    memory_clock[0] = 110.0
    assert cache.replace("first", "revived") is False
    assert cache.get("first") is None
    assert cache.replace("absent", "new") is False
    assert cache.count() == 0


def test_memory_revision_preserves_expiry_event_key_and_metadata(report_pair, memory_clock):
    event, report = report_pair
    store = MemoryStore(10, 2)
    saved = store.save(event, report)
    assert saved["metadata"]["report_revision"] == 1
    assert report.metadata["report_revision"] == 99
    memory_clock[0] = 105.0
    updated = store.update_report(report.incident_id, report.model_copy(update={"summary": "New evidence"}), 1)
    assert updated["summary"] == "New evidence"
    assert updated["metadata"] == {"report_revision": 2, "investigation_mode": "deterministic"}
    assert store.cache.items("event:")[0][0] == f"event:{event_key(event)}"
    assert store.save(event, report.model_copy(update={"incident_id": "another"})) == updated
    assert store.get(report.incident_id) == updated
    assert store.list(10, 0)["items"] == [updated]
    memory_clock[0] = 110.0
    assert store.update_report(report.incident_id, report, 2) is None
    assert store.get(report.incident_id) is None
    assert store.list(10, 0)["total"] == 0


def test_memory_conflict_and_missing_report(report_pair):
    event, report = report_pair
    store = MemoryStore(10, 2)
    assert store.update_report(report.incident_id, report, 1) is None
    store.save(event, report)
    updated = store.update_report(report.incident_id, report, 1)
    with pytest.raises(ReportConflictError, match="^Report revision conflict$"):
        store.update_report(report.incident_id, report, 1)
    assert store.get(report.incident_id) == updated
    assert store.update_report(report.incident_id, report, 2)["metadata"]["report_revision"] == 3


def test_memory_expiry_during_update_cannot_revive_report(report_pair, memory_clock, monkeypatch):
    import app.storage as storage

    event, report = report_pair
    store = MemoryStore(10, 2)
    store.save(event, report)
    bounded_report = storage._bounded_report

    def expire_during_serialization(data, revision):
        result = bounded_report(data, revision)
        memory_clock[0] = 110.0
        return result

    monkeypatch.setattr(storage, "_bounded_report", expire_during_serialization)
    assert store.update_report(report.incident_id, report, 1) is None
    assert store.get(report.incident_id) is None


@pytest.mark.parametrize("expected_revision", [0, -1, True, 1.0, "1"])
def test_memory_rejects_invalid_expected_revision(report_pair, expected_revision):
    event, report = report_pair
    store = MemoryStore(10, 2)
    saved = store.save(event, report)
    with pytest.raises(ReportConflictError):
        store.update_report(report.incident_id, report, expected_revision)
    assert store.get(report.incident_id) == saved


@pytest.mark.parametrize("field,value", [
    ("incident_id", "different"),
    ("provider", "azure"),
    ("resource_id", "different"),
    ("resource_type", "different"),
])
def test_memory_identity_guard(report_pair, field, value):
    event, report = report_pair
    store = MemoryStore(10, 2)
    saved = store.save(event, report)
    candidate = RCAReport.model_validate({**report.model_dump(), field: value})
    with pytest.raises(ValueError, match="^Report identity does not match stored incident$"):
        store.update_report(report.incident_id, candidate, 1)
    assert store.get(report.incident_id) == saved


def test_memory_legacy_revision_defaults_to_one(report_pair):
    event, report = report_pair
    store = MemoryStore(10, 2)
    legacy = report.model_dump(mode="json")
    legacy["metadata"].pop("report_revision")
    store.cache.set(f"event:{event_key(event)}", legacy)
    assert store.get(report.incident_id)["metadata"]["report_revision"] == 1
    assert store.list(10, 0)["items"][0]["metadata"]["report_revision"] == 1
    assert store.save(event, report)["metadata"]["report_revision"] == 1
    assert store.update_report(report.incident_id, report, 1)["metadata"]["report_revision"] == 2


def test_memory_callers_cannot_change_trusted_revision(report_pair):
    event, report = report_pair
    store = MemoryStore(10, 2)
    for snapshot in (store.save(event, report), store.get(report.incident_id), store.list(10, 0)["items"][0]):
        snapshot["metadata"]["report_revision"] = 999
        snapshot["resource_id"] = "changed"
    updated = store.update_report(report.incident_id, report, 1)
    updated["metadata"]["report_revision"] = 999
    assert store.get(report.incident_id)["metadata"]["report_revision"] == 2


def test_memory_concurrent_updates_have_one_winner(report_pair):
    event, report = report_pair
    store = MemoryStore(10, 2)
    store.save(event, report)
    barrier = Barrier(2)

    def update():
        barrier.wait(timeout=5)
        try:
            return store.update_report(report.incident_id, report, 1)
        except ReportConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: update(), range(2)))
    assert results.count("conflict") == 1
    assert store.get(report.incident_id)["metadata"]["report_revision"] == 2


@pytest.mark.parametrize("operation", ["save", "update"])
def test_memory_report_size_boundary(report_pair, operation):
    event, report = report_pair
    store = MemoryStore(10, 2)
    saved = store.save(event, report)
    data = report.model_dump(mode="json")
    data["metadata"]["report_revision"] = 1 if operation == "save" else 2
    data["summary"] = ""
    remaining = 64 * 1024 - len(json.dumps(data).encode("utf-8"))
    candidate = report.model_copy(update={"summary": "x" * remaining})
    oversized = candidate.model_copy(update={"summary": candidate.summary + "x"})
    with pytest.raises(ReportTooLarge, match="^Report exceeds storage size limit$"):
        if operation == "save":
            store.save(event, oversized)
        else:
            store.update_report(report.incident_id, oversized, 1)
    assert store.get(report.incident_id) == saved
    if operation == "save":
        store.cache.delete(f"event:{event_key(event)}")
        result = store.save(event, candidate)
    else:
        result = store.update_report(report.incident_id, candidate, 1)
    assert len(json.dumps(result).encode("utf-8")) == 64 * 1024


def test_postgres_update_locks_row_and_only_changes_report(report_pair, monkeypatch):
    _, report = report_pair
    current = report.model_dump(mode="json")
    current["metadata"].pop("report_revision")
    original = deepcopy(current)
    connection = MagicMock()
    store = PostgresStore("unused", 10, 2)
    monkeypatch.setattr(store, "connect", lambda: connection)
    connection.__enter__.return_value = connection

    def fetchone():
        if connection.execute.call_count == 1:
            return (current,)
        return (connection.execute.call_args.args[1][0].obj,)

    connection.execute.return_value.fetchone.side_effect = fetchone
    result = store.update_report(report.incident_id, report, 1)
    select, update = connection.execute.call_args_list
    assert "FOR UPDATE" in select.args[0]
    assert "expires_at > clock_timestamp()" in select.args[0]
    assert select.args[1] == (report.incident_id,)
    assert update.args[0] == "UPDATE rca_reports SET report = %s WHERE incident_id = %s AND expires_at > clock_timestamp() RETURNING report"
    assert update.args[1][1] == report.incident_id
    assert result["metadata"]["report_revision"] == 2
    assert current == original
    connection.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize("expires_during_update", [False, True])
def test_postgres_missing_or_expired_during_update(report_pair, monkeypatch, expires_during_update):
    _, report = report_pair
    current = report.model_dump(mode="json")
    current["metadata"]["report_revision"] = 1
    rows = [(current,), None] if expires_during_update else [None]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.side_effect = rows
    store = PostgresStore("unused", 10, 2)
    monkeypatch.setattr(store, "connect", lambda: connection)
    assert store.update_report(report.incident_id, report, 1) is None
    assert connection.execute.call_count == len(rows)


@pytest.mark.parametrize("rejection", ["conflict", "identity", "oversized", "database"])
def test_postgres_update_rejections_roll_back_with_generic_errors(report_pair, monkeypatch, rejection):
    _, report = report_pair
    current = report.model_dump(mode="json")
    current["metadata"]["report_revision"] = 1
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (current,)
    store = PostgresStore("unused", 10, 2)
    monkeypatch.setattr(store, "connect", lambda: connection)
    expected_revision = 1
    candidate = report
    if rejection == "conflict":
        expected_revision = 2
        error_type, message = ReportConflictError, "Report revision conflict"
    elif rejection == "identity":
        candidate = report.model_copy(update={"resource_id": "private resource"})
        error_type, message = ValueError, "Report identity does not match stored incident"
    elif rejection == "oversized":
        candidate = report.model_copy(update={"summary": "x" * (64 * 1024)})
        error_type, message = ReportTooLarge, "Report exceeds storage size limit"
    else:
        connection.execute.side_effect = [connection.execute.return_value, psycopg.OperationalError("private credentials")]
        error_type, message = StorageUnavailable, "Database write failed"
    with pytest.raises(error_type, match=f"^{message}$"):
        store.update_report(report.incident_id, candidate, expected_revision)
    assert connection.execute.call_count == (2 if rejection == "database" else 1)
    assert connection.__exit__.call_args.args[0] is (psycopg.OperationalError if rejection == "database" else error_type)


@pytest.mark.parametrize("operation", ["save", "update"])
def test_postgres_errors_are_generic(report_pair, monkeypatch, operation):
    event, report = report_pair
    store = PostgresStore("unused", 10, 2)

    def unavailable():
        raise psycopg.OperationalError("private database credentials")

    monkeypatch.setattr(store, "connect", unavailable)
    with pytest.raises(StorageUnavailable, match="^Database write failed$") as error:
        if operation == "save":
            store.save(event, report)
        else:
            store.update_report(report.incident_id, report, 1)
    assert error.value.__suppress_context__


def test_postgres_oversized_save_does_not_connect(report_pair, monkeypatch):
    event, report = report_pair
    store = PostgresStore("unused", 10, 2)
    connect = MagicMock()
    monkeypatch.setattr(store, "connect", connect)
    with pytest.raises(ReportTooLarge):
        store.save(event, report.model_copy(update={"summary": "\u00e9" * (64 * 1024)}))
    connect.assert_not_called()
