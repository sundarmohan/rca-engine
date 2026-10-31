from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import timezone

import psycopg
from psycopg.types.json import Jsonb

from app.config import settings
from app.models import IncomingEvent, RCAReport
from app.retention.cache import TTLCache


class StorageUnavailable(RuntimeError):
    pass


class ReportConflictError(RuntimeError):
    pass


class ReportTooLarge(RuntimeError):
    pass


def _report_snapshot(data: dict) -> dict:
    snapshot = deepcopy(data)
    snapshot.setdefault("metadata", {}).setdefault("report_revision", 1)
    return snapshot


def _bounded_report(data: dict, revision: int) -> dict:
    data = deepcopy(data)
    data.setdefault("metadata", {})["report_revision"] = revision
    if len(json.dumps(data).encode("utf-8")) > 64 * 1024:
        raise ReportTooLarge("Report exceeds storage size limit")
    return data


def _revised_report(incident_id: str, current: dict, data: dict, expected_revision: int) -> dict:
    if data["incident_id"] != incident_id or any(
        data[field] != current[field]
        for field in ("incident_id", "provider", "resource_id", "resource_type")
    ):
        raise ValueError("Report identity does not match stored incident")
    revision = current.get("metadata", {}).get("report_revision", 1)
    if (
        type(expected_revision) is not int or expected_revision < 1
        or type(revision) is not int or revision < 1 or revision != expected_revision
    ):
        raise ReportConflictError("Report revision conflict")
    return _bounded_report(data, revision + 1)


def event_key(event: IncomingEvent) -> str:
    timestamp = event.occurred_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    normalized = event.model_dump(mode="json")
    normalized["occurred_at"] = timestamp.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


class MemoryStore:
    mode = "memory"

    def __init__(self, ttl_seconds: int, max_items: int):
        self.cache = TTLCache(ttl_seconds, max_items)
        self._lock = threading.RLock()

    def initialize(self) -> None:
        pass

    def ready(self) -> bool:
        return True

    def purge(self) -> None:
        self.cache.count()

    def save(self, event: IncomingEvent, report: RCAReport) -> dict:
        data = _bounded_report(report.model_dump(mode="json"), 1)
        with self._lock:
            key = f"event:{event_key(event)}"
            existing = self.cache.get(key)
            if existing:
                return _report_snapshot(existing)
            self.cache.set(key, data)
            return _report_snapshot(data)

    def update_report(self, incident_id: str, report: RCAReport, expected_revision: int) -> dict | None:
        data = report.model_dump(mode="json")
        with self._lock:
            entry = next((item for item in self.cache.items("event:") if item[1]["incident_id"] == incident_id), None)
            if entry is None:
                return None
            key, current = entry
            revised = _revised_report(incident_id, current, data, expected_revision)
            if not self.cache.replace(key, revised):
                return None
            return _report_snapshot(revised)

    def get(self, incident_id: str) -> dict | None:
        with self._lock:
            report = next((report for report in self.cache.values("event:") if report["incident_id"] == incident_id), None)
            return _report_snapshot(report) if report is not None else None

    def list(self, limit: int, offset: int) -> dict:
        with self._lock:
            reports = sorted(self.cache.values("event:"), key=lambda report: report["detected_at"], reverse=True)
            return {"items": [_report_snapshot(report) for report in reports[offset:offset + limit]], "total": len(reports)}


class PostgresStore:
    mode = "postgresql"

    def __init__(self, database_url: str, ttl_seconds: int, max_items: int):
        self.database_url = database_url
        self.ttl_seconds = max(1, ttl_seconds)
        self.max_items = max(1, max_items)

    def connect(self):
        return psycopg.connect(self.database_url, connect_timeout=5, options="-c statement_timeout=10000")

    def initialize(self) -> None:
        try:
            with self.connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(72819461)")
                connection.execute("CREATE TABLE IF NOT EXISTS rca_schema_versions (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                connection.execute("CREATE TABLE IF NOT EXISTS rca_reports (incident_id text PRIMARY KEY, event_key text UNIQUE NOT NULL, report jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS rca_reports_expiry_idx ON rca_reports (expires_at)")
                connection.execute("CREATE INDEX IF NOT EXISTS rca_reports_created_idx ON rca_reports (created_at DESC, incident_id)")
                connection.execute("INSERT INTO rca_schema_versions (version) VALUES (1) ON CONFLICT DO NOTHING")
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
        except psycopg.Error:
            raise StorageUnavailable("Database initialization failed") from None

    def ready(self) -> bool:
        try:
            with self.connect() as connection:
                row = connection.execute("SELECT version FROM rca_schema_versions WHERE version = 1").fetchone()
            return row is not None
        except psycopg.Error:
            return False

    def purge(self) -> None:
        try:
            with self.connect() as connection:
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
        except psycopg.Error:
            raise StorageUnavailable("Database cleanup failed") from None

    def save(self, event: IncomingEvent, report: RCAReport) -> dict:
        data = _bounded_report(report.model_dump(mode="json"), 1)
        try:
            with self.connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(72819461)")
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                row = connection.execute(
                    "INSERT INTO rca_reports (incident_id, event_key, report, expires_at) VALUES (%s, %s, %s, now() + %s * interval '1 second') ON CONFLICT (event_key) DO UPDATE SET event_key = EXCLUDED.event_key RETURNING report",
                    (report.incident_id, event_key(event), Jsonb(data), self.ttl_seconds),
                ).fetchone()
                connection.execute("DELETE FROM rca_reports WHERE incident_id IN (SELECT incident_id FROM rca_reports ORDER BY created_at DESC, incident_id OFFSET %s)", (self.max_items,))
                return _report_snapshot(row[0])
        except psycopg.Error:
            raise StorageUnavailable("Database write failed") from None

    def update_report(self, incident_id: str, report: RCAReport, expected_revision: int) -> dict | None:
        data = report.model_dump(mode="json")
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT report FROM rca_reports WHERE incident_id = %s AND expires_at > clock_timestamp() FOR UPDATE",
                    (incident_id,),
                ).fetchone()
                if row is None:
                    return None
                revised = _revised_report(incident_id, row[0], data, expected_revision)
                updated = connection.execute(
                    "UPDATE rca_reports SET report = %s WHERE incident_id = %s AND expires_at > clock_timestamp() RETURNING report",
                    (Jsonb(revised), incident_id),
                ).fetchone()
                return _report_snapshot(updated[0]) if updated is not None else None
        except psycopg.Error:
            raise StorageUnavailable("Database write failed") from None

    def get(self, incident_id: str) -> dict | None:
        try:
            with self.connect() as connection:
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                row = connection.execute("SELECT report FROM rca_reports WHERE incident_id = %s AND expires_at > now()", (incident_id,)).fetchone()
                return _report_snapshot(row[0]) if row else None
        except psycopg.Error:
            raise StorageUnavailable("Database read failed") from None

    def list(self, limit: int, offset: int) -> dict:
        try:
            with self.connect() as connection:
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                total = connection.execute("SELECT count(*) FROM rca_reports WHERE expires_at > now()").fetchone()[0]
                rows = connection.execute("SELECT report FROM rca_reports WHERE expires_at > now() ORDER BY created_at DESC, incident_id LIMIT %s OFFSET %s", (limit, offset)).fetchall()
                return {"items": [_report_snapshot(row[0]) for row in rows], "total": total}
        except psycopg.Error:
            raise StorageUnavailable("Database read failed") from None


store = (
    PostgresStore(settings.database_url, settings.cache_ttl_seconds, settings.cache_max_items)
    if settings.database_url else MemoryStore(settings.cache_ttl_seconds, settings.cache_max_items)
)
