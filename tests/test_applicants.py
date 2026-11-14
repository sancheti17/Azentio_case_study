import json
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient

from kyc.api import create_app
from kyc.audit import AuditStore
from kyc.cross_checks import cross_document_reason_codes, name_similarity
from kyc.generate import generate
from kyc.pipeline import Pipeline
from kyc.providers import FakeProvider
from kyc.review_store import ReviewStore
from kyc.rules import verhoeff_digit


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def test_name_match_tolerates_initials_and_token_order():
    assert name_similarity("Anita M Rao", "Rao, A. M.") >= 0.80


def test_cross_document_codes_match_all_seeded_ground_truth(tmp_path):
    data = tmp_path / "generated"
    labels = generate(30, 42, data)
    applicants = _read_jsonl(data / "applicants.jsonl")
    grouped = {}
    for row in labels:
        grouped.setdefault(row["applicant_id"], {})[row["doc_type"]] = row["fields"]
    assert len(applicants) == 30
    seeded = [a for a in applicants if a["expected_reason_codes"]]
    assert len(seeded) >= 8
    covered = set()
    for applicant in applicants:
        actual = cross_document_reason_codes(grouped[applicant["applicant_id"]], applicant["as_of"])
        expected = applicant["expected_reason_codes"]
        assert actual == expected, applicant["applicant_id"]
        covered.update(actual)
    assert covered == {"NAME_MISMATCH", "DOB_MISMATCH", "PAN_SURNAME_INITIAL_MISMATCH", "ADDRESS_PROOF_STALE"}


def test_review_resolve_appends_correction_and_audit_once(tmp_path, caplog):
    data = tmp_path / "data"
    labels = generate(1, 42, data)
    applicant_id = "app_0001"
    documents = []
    for row in labels:
        documents.append({"doc_id":row["doc_id"], "text":(data / (row["doc_id"] + ".txt")).read_text(encoding="utf-8")})
    db_path = tmp_path / "audit.sqlite3"
    corrections_path = tmp_path / "corrections.jsonl"
    store = ReviewStore(db_path, corrections_path)
    pipeline = Pipeline(FakeProvider(), audit=AuditStore(str(db_path)))
    client = TestClient(create_app(pipeline, store))
    response = client.post(f"/v1/applicants/{applicant_id}/verify", json={"as_of":"2026-11-14", "documents":documents})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "REVIEW"
    item_id = body["review_item_id"]
    queued = client.get("/v1/review-items?status=open").json()["items"]
    assert len(queued) == 1 and queued[0]["review_id"] == item_id
    corrected_doc = next(d["doc_id"] for d in documents if d["doc_id"].endswith("_pan"))
    resolve = client.post(f"/v1/review-items/{item_id}/resolve", json={
        "reviewer":"synthetic-reviewer", "decision":"AUTO_APPROVE",
        "corrected_fields":{corrected_doc:{"name":"SPECIMEN NEXORA A"}}
    })
    assert resolve.status_code == 200, resolve.text
    assert client.get("/v1/review-items?status=open").json()["items"] == []
    correction_rows = _read_jsonl(corrections_path)
    assert correction_rows == [{"applicant_id":"app_0001", "doc_id":corrected_doc,
                                "doc_type":"pan", "fields":{"name":"SPECIMEN NEXORA A"}}]
    again = client.post(f"/v1/review-items/{item_id}/resolve", json={
        "reviewer":"synthetic-reviewer", "decision":"REJECT", "corrected_fields":{}
    })
    assert again.status_code == 409
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT actor,applicant_id,decision,reason_codes_json,event_type FROM applicant_audit ORDER BY audit_id").fetchall()
        assert len(rows) == 2
        assert [r[4] for r in rows] == ["decision", "resolution"]
        assert rows[-1][2] == "AUTO_APPROVE"
        assert json.loads(rows[-1][3]) == body["reason_codes"]
        assert db.execute("SELECT status FROM review_items WHERE review_id=?", (item_id,)).fetchone()[0] == "resolved"
    # The synthetic identifier is present in OCR but must not enter the audit DB or logs.
    identifier = next(row["fields"]["id_number"] for row in labels if row["doc_type"] == "id_card")
    assert len(identifier) == 12 and identifier[0] in "01"
    assert identifier.encode() not in db_path.read_bytes()
    assert identifier not in caplog.text
