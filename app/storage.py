from __future__ import annotations

import hashlib
import json
import threading
from datetime import timezone

import psycopg
from psycopg.types.json import Jsonb

from app.config import settings
from app.models import IncomingEvent, RCAReport
from app.retention.cache import TTLCache


class StorageUnavailable(RuntimeError):
    pass


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
        with self._lock:
            key = f"event:{event_key(event)}"
            existing = self.cache.get(key)
            if existing:
                return existing
            data = report.model_dump(mode="json")
            self.cache.set(key, data)
            return data

    def get(self, incident_id: str) -> dict | None:
        return next((report for report in self.cache.values("event:") if report["incident_id"] == incident_id), None)

    def list(self, limit: int, offset: int) -> dict:
        reports = sorted(self.cache.values("event:"), key=lambda report: report["detected_at"], reverse=True)
        return {"items": reports[offset:offset + limit], "total": len(reports)}


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
        try:
            with self.connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(72819461)")
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                row = connection.execute(
                    "INSERT INTO rca_reports (incident_id, event_key, report, expires_at) VALUES (%s, %s, %s, now() + %s * interval '1 second') ON CONFLICT (event_key) DO UPDATE SET event_key = EXCLUDED.event_key RETURNING report",
                    (report.incident_id, event_key(event), Jsonb(report.model_dump(mode="json")), self.ttl_seconds),
                ).fetchone()
                connection.execute("DELETE FROM rca_reports WHERE incident_id IN (SELECT incident_id FROM rca_reports ORDER BY created_at DESC, incident_id OFFSET %s)", (self.max_items,))
                return row[0]
        except psycopg.Error:
            raise StorageUnavailable("Database write failed") from None

    def get(self, incident_id: str) -> dict | None:
        try:
            with self.connect() as connection:
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                row = connection.execute("SELECT report FROM rca_reports WHERE incident_id = %s AND expires_at > now()", (incident_id,)).fetchone()
                return row[0] if row else None
        except psycopg.Error:
            raise StorageUnavailable("Database read failed") from None

    def list(self, limit: int, offset: int) -> dict:
        try:
            with self.connect() as connection:
                connection.execute("DELETE FROM rca_reports WHERE expires_at <= now()")
                total = connection.execute("SELECT count(*) FROM rca_reports WHERE expires_at > now()").fetchone()[0]
                rows = connection.execute("SELECT report FROM rca_reports WHERE expires_at > now() ORDER BY created_at DESC, incident_id LIMIT %s OFFSET %s", (limit, offset)).fetchall()
                return {"items": [row[0] for row in rows], "total": total}
        except psycopg.Error:
            raise StorageUnavailable("Database read failed") from None


store = (
    PostgresStore(settings.database_url, settings.cache_ttl_seconds, settings.cache_max_items)
    if settings.database_url else MemoryStore(settings.cache_ttl_seconds, settings.cache_max_items)
)
