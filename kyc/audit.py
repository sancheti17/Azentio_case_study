"""SQLite audit trail: hashes and masked decisions only; never raw OCR/output."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .models import ExtractResponse


class AuditStore:
    def __init__(self, path: str = "kyc-audit.sqlite3"):
        self.path = path
        if path == ":memory:":
            raise ValueError("Use a file path: audit connections are per request")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                threshold REAL NOT NULL,
                decision_date TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                events_json TEXT NOT NULL,
                response_json TEXT NOT NULL
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, response: ExtractResponse, *, source_sha256: str, provider: str,
               threshold: float, decision_date: str, attempts: int, events: list[str]) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO decisions
                (created_at, source_sha256, provider, pipeline_version, threshold,
                 decision_date, attempts, events_json, response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                datetime.now(timezone.utc).isoformat(), source_sha256, provider,
                "0.1.0/rules-v1/confidence-v1", threshold, decision_date, attempts,
                json.dumps(events), response.model_dump_json(exclude_none=True),
            ))
