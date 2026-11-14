"""SQLite applicant review queue, append-only audit, and labelled corrections."""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .models import SCHEMAS

_ID_RE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")

def _mask(text):
    return _ID_RE.sub(lambda m: "XXXXXXXX" + m.group(0)[-4:], str(text))

class ReviewStore:
    def __init__(self, db_path="kyc-audit.sqlite3", corrections_path="data/corrections.jsonl"):
        self.db_path = str(db_path)
        self.corrections_path = Path(corrections_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS review_items (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('open','resolved')),
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                documents_json TEXT NOT NULL,
                low_confidence_fields_json TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                resolved_decision TEXT
            )""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_review_per_applicant ON review_items(applicant_id) WHERE status='open'")
            db.execute("""CREATE TABLE IF NOT EXISTS applicant_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                applicant_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('decision','resolution'))
            )""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS applicant_audit_no_update
                BEFORE UPDATE ON applicant_audit BEGIN SELECT RAISE(ABORT, 'append-only audit'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS applicant_audit_no_delete
                BEFORE DELETE ON applicant_audit BEGIN SELECT RAISE(ABORT, 'append-only audit'); END""")

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def record_decision(self, applicant_id, decision, reason_codes, documents, low_confidence_fields):
        safe_applicant = _mask(applicant_id)
        safe_documents = [{"doc_id":_mask(d["doc_id"]), "doc_type":d.get("doc_type")} for d in documents]
        safe_low = [{**item, "doc_id":_mask(item.get("doc_id", ""))} for item in low_confidence_fields]
        codes_json = json.dumps(sorted(set(reason_codes)), ensure_ascii=True)
        item_id = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO applicant_audit(timestamp,actor,applicant_id,decision,reason_codes_json,event_type) VALUES(?,?,?,?,?,?)",
                       (self._now(), "system", safe_applicant, decision, codes_json, "decision"))
            if decision == "REVIEW":
                row = db.execute("SELECT review_id FROM review_items WHERE applicant_id=? AND status='open'", (safe_applicant,)).fetchone()
                if row:
                    item_id = row[0]
                else:
                    cur = db.execute("""INSERT INTO review_items(applicant_id,status,created_at,documents_json,
                        low_confidence_fields_json,reason_codes_json) VALUES(?, 'open', ?, ?, ?, ?)""",
                        (safe_applicant, self._now(), json.dumps(safe_documents), json.dumps(safe_low), codes_json))
                    item_id = cur.lastrowid
        return item_id

    def list_items(self, status="open"):
        with self._connect() as db:
            if status == "all":
                rows = db.execute("SELECT review_id,applicant_id,status,created_at,resolved_at,low_confidence_fields_json,reason_codes_json,resolved_decision FROM review_items ORDER BY review_id").fetchall()
            else:
                rows = db.execute("SELECT review_id,applicant_id,status,created_at,resolved_at,low_confidence_fields_json,reason_codes_json,resolved_decision FROM review_items WHERE status=? ORDER BY review_id", (status,)).fetchall()
        return [{"review_id":r[0], "applicant_id":r[1], "status":r[2], "created_at":r[3],
                 "resolved_at":r[4], "low_confidence_fields":json.loads(r[5]),
                 "reason_codes":json.loads(r[6]), "resolved_decision":r[7]} for r in rows]

    def resolve(self, review_id, reviewer, decision, corrected_fields):
        """Resolve once. Corrections JSONL is written only for explicitly corrected docs."""
        conn = self._connect()
        old_size = None
        appended = False
        outcome = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT applicant_id,status,documents_json,reason_codes_json FROM review_items WHERE review_id=?", (review_id,)).fetchone()
            if row is None:
                outcome = ("not_found", None)
            elif row[1] != "open":
                outcome = ("already_resolved", None)
            else:
                applicant_id, _, documents_json, review_reason_codes = row
                doc_types = {item["doc_id"]: item.get("doc_type") for item in json.loads(documents_json)}
                corrections = []
                for doc_id, values in (corrected_fields or {}).items():
                    safe_doc_id = _mask(doc_id)
                    doc_type = doc_types.get(safe_doc_id)
                    if not doc_type or doc_type not in SCHEMAS:
                        conn.rollback()
                        return ("invalid_correction", None)
                    allowed = set(SCHEMAS[doc_type].model_fields)
                    if not values or not set(values).issubset(allowed) or any(not isinstance(v, str) for v in values.values()):
                        conn.rollback()
                        return ("invalid_correction", None)
                    corrections.append({"doc_id":safe_doc_id, "applicant_id":applicant_id,
                                        "doc_type":doc_type, "fields":values})
                if corrections:
                    self.corrections_path.parent.mkdir(parents=True, exist_ok=True)
                    if self.corrections_path.exists():
                        old_size = self.corrections_path.stat().st_size
                    else:
                        old_size = 0
                    payload = "".join(json.dumps(line, sort_keys=True, ensure_ascii=True) + "\n" for line in corrections)
                    with self.corrections_path.open("a", encoding="utf-8") as f:
                        f.write(payload)
                        f.flush()
                        os.fsync(f.fileno())
                    appended = True
                now = self._now()
                conn.execute("UPDATE review_items SET status='resolved',resolved_at=?,resolved_decision=? WHERE review_id=? AND status='open'",
                             (now, decision, review_id))
                conn.execute("INSERT INTO applicant_audit(timestamp,actor,applicant_id,decision,reason_codes_json,event_type) VALUES(?,?,?,?,?,?)",
                             (now, _mask(reviewer), applicant_id, decision, review_reason_codes, "resolution"))
                conn.commit()
                outcome = ("resolved", {"review_id":review_id, "applicant_id":applicant_id,
                                         "status":"resolved", "decision":decision,
                                         "correction_documents":len(corrections), "resolved_at":now})
        except Exception:
            conn.rollback()
            if appended and old_size is not None:
                try:
                    with self.corrections_path.open("r+b") as f:
                        f.truncate(old_size)
                except OSError:
                    pass
            raise
        finally:
            conn.close()
        return outcome
