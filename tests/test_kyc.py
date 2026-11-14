import builtins
import json
import math
import re
import sqlite3
from datetime import date
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from kyc.api import create_app
from kyc.audit import AuditStore
from kyc.evaluate import evaluate
from kyc.generate import generate
from kyc.pipeline import Pipeline
from kyc.providers import FakeProvider, get_provider
from kyc.rules import normalize_date, pan_valid, verhoeff_digit, verhoeff_valid

TODAY = date(2026, 1, 1)
ID = "01234567890" + verhoeff_digit("01234567890")
FIELDS = {
    "pan": {"name":"SPECIMEN NEXORA", "father_name":"SPECIMEN PARENT", "dob":"12-Mar-1991", "pan_number":"SYNPT2345A"},
    "id_card": {"name":"SPECIMEN NEXORA", "dob":"12/03/1991", "gender":"FEMALE", "id_number":ID,
                "address":"12 MODEL AVENUE", "pincode":"560001"},
    "address_proof": {"name":"SPECIMEN NEXORA", "address":"12 MODEL AVENUE", "pincode":"560001", "issue_date":"2024-05-10"},
}


def text(kind, overrides=None):
    headers = {"pan":"PAN CARD", "id_card":"GOVERNMENT ID AADHAAR STYLE", "address_proof":"UTILITY BILL"}
    fields = dict(FIELDS[kind]); fields.update(overrides or {})
    return "SPECIMEN\n" + headers[kind] + "\n" + "\n".join(k.replace("_", " ") + ": " + v for k, v in fields.items())


@pytest.mark.parametrize("kind", list(FIELDS))
def test_happy_paths(kind):
    result = Pipeline(FakeProvider(), today=TODAY).extract("test", text(kind))
    assert result.status == "ok"
    assert set(result.fields) == set(FIELDS[kind])
    assert all(f.confidence == 1.0 for f in result.fields.values())
    if "dob" in result.fields:
        assert result.fields["dob"].value == "1991-03-12"


def test_default_offline_provider():
    assert isinstance(get_provider(), FakeProvider)
    assert Pipeline(today=TODAY).extract("test", text("pan")).status == "ok"


def test_generator_reproducible_and_safe(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    rows = generate(30, 42, a)
    generate(30, 42, b)
    assert {p.name:p.read_bytes() for p in a.iterdir()} == {p.name:p.read_bytes() for p in b.iterdir()}
    assert len(list(a.glob("*.txt"))) == 90
    assert len(rows) == 90
    assert sum(r["noisy"] for r in rows) >= math.ceil(0.25 * 90)
    assert all("SPECIMEN" in p.read_text() for p in a.glob("*.txt"))
    for row in rows:
        if row["doc_type"] == "id_card":
            identifier = row["fields"]["id_number"]
            assert re.fullmatch(r"[01][0-9]{11}", identifier)
            assert verhoeff_valid(identifier)
    for p in a.glob("*.txt"):
        assert all(m[0] in "01" for m in re.findall(r"(?<![0-9])[0-9]{12}(?![0-9])", p.read_text()))
    with pytest.raises(FileExistsError):
        generate(30, 42, a)


def test_fake_never_reads_labels(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Provider must not read files")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    assert Pipeline(FakeProvider(), today=TODAY).extract("x", text("pan")).status == "ok"


@pytest.mark.parametrize("fault", ["malformed", "missing_field"])
def test_retry_then_success(fault):
    class Recording(FakeProvider):
        def __init__(self):
            super().__init__([fault]); self.prompts = []
        def extract(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().extract(**kwargs)
    provider = Recording()
    result = Pipeline(provider, today=TODAY).extract("x", text("pan"))
    assert result.status == "ok"
    assert provider.calls == 2
    assert "Previous validation error:" in provider.prompts[1]
    assert ("JSON_INVALID" if fault == "malformed" else "name:missing") in provider.prompts[1]


@pytest.mark.parametrize("fault", ["malformed", "missing_field", "raise"])
def test_retry_exhausted_no_exception(fault):
    provider = FakeProvider([fault, fault])
    result = Pipeline(provider).extract("x", text("id_card"))
    assert result.status == "failed"
    assert result.fields == {}
    assert provider.calls == 2
    assert ID not in result.model_dump_json()


@pytest.mark.parametrize("value,valid", [("2363", True), ("123451", True), ("2364", False), ("123450", False), ("", False), ("１２３", False)])
def test_known_verhoeff(value, valid):
    # Public algorithm vectors, not 12-digit real identity numbers.
    assert verhoeff_valid(value) is valid


@pytest.mark.parametrize("value,valid", [("SYNPT2345A", True), ("SYNCT2345A", False), ("synpt2345a", False), ("SYNPT234AA", False), ("SYNPT2345AA", False)])
def test_pan_format(value, valid):
    assert pan_valid(value) is valid


@pytest.mark.parametrize("value", ["1991-03-12", "12/03/1991", "12-Mar-1991", "12-03-1991"])
def test_date_formats(value):
    assert normalize_date(value) == "1991-03-12"


@pytest.mark.parametrize("value", ["31/02/2024", "03/04/24", "2024", "tomorrow"])
def test_bad_dates(value):
    with pytest.raises(ValueError):
        normalize_date(value)


@pytest.mark.parametrize("kind,changes,field,reason", [
    ("pan", {"pan_number":"SYNCT2345A"}, "pan_number", "PAN_FORMAT_INVALID"),
    ("id_card", {"id_number":ID[:-1] + str((int(ID[-1])+1)%10)}, "id_number", "ID_CHECKSUM_FAILED"),
    ("id_card", {"pincode":"056001"}, "pincode", "PINCODE_INVALID"),
    ("pan", {"dob":"31/02/1991"}, "dob", "DATE_INVALID"),
    ("address_proof", {"issue_date":"2026-01-02"}, "issue_date", "DATE_INVALID"),
])
def test_rule_violations(kind, changes, field, reason):
    result = Pipeline(FakeProvider(), threshold=0.0, today=TODAY).extract("x", text(kind, changes))
    assert result.status == "needs_review"
    assert reason in result.fields[field].reason_codes
    assert result.fields[field].confidence <= 0.49


def test_issue_date_today_and_leap_day():
    assert normalize_date("29-Feb-2024") == "2024-02-29"
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text("address_proof", {"issue_date":"2026-01-01"}))
    assert result.status == "ok"


def test_confidence_ocr_correction():
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text("id_card", {"id_number":ID.replace("0", "O", 1)}))
    assert result.status == "needs_review"
    assert result.fields["id_number"].value == "XXXXXXXX" + ID[-4:]
    assert result.fields["id_number"].confidence == 0.8
    assert "OCR_CORRECTED" in result.fields["id_number"].reason_codes


def test_threshold_strict_less_than():
    assert Pipeline(FakeProvider(), threshold=1, today=TODAY).extract("x", text("pan")).status == "ok"
    for bad in [float("nan"), float("inf"), -0.1, 1.01]:
        with pytest.raises(ValueError):
            Pipeline(FakeProvider(), threshold=bad)


class StaticProvider(FakeProvider):
    def __init__(self, output):
        super().__init__()
        self.output = output
    def extract(self, **kwargs):
        return self.output


@pytest.mark.parametrize("output", ["[]", "null", '"hello"', '{"fields":null}', '{"fields":{"name":null}}',
    '{"fields":{},"fields":{}}', '{"fields":{},"score":NaN}', '```json\n{}\n```',
    json.dumps({"fields":dict(FIELDS["pan"], unexpected=ID)}), "[" * 2000, None, "x" * 100001])
def test_arbitrary_bad_model_output(output):
    result = Pipeline(StaticProvider(output)).extract("x", text("pan"))
    assert result.status == "failed"
    assert ID not in result.model_dump_json()


def test_hallucination_and_conflicting_source():
    payload = dict(FIELDS["pan"], name="UNSUPPORTED PERSON")
    result = Pipeline(StaticProvider(json.dumps({"fields":payload})), today=TODAY).extract("x", text("pan"))
    assert result.status == "needs_review"
    assert result.fields["name"].confidence == 0.55
    assert "SOURCE_NOT_FOUND" in result.fields["name"].reason_codes
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text("pan") + "\nName: CONFLICTING PERSON")
    assert result.status == "needs_review"
    assert "SOURCE_CONFLICT" in result.fields["name"].reason_codes


@pytest.mark.parametrize("input_text,error", [("SPECIMEN no recognizable header", "DOCUMENT_TYPE_UNKNOWN"),
    ("SPECIMEN PAN CARD GOVERNMENT ID", "DOCUMENT_TYPE_AMBIGUOUS")])
def test_classifier_fails_closed(input_text, error):
    provider = FakeProvider()
    result = Pipeline(provider).extract("x", input_text)
    assert result.status == "failed"
    assert result.error == error
    assert provider.calls == 0


def test_api_masking_audit_and_errors(tmp_path, caplog):
    database = tmp_path / "audit.sqlite3"
    service = Pipeline(FakeProvider(), today=TODAY, audit=AuditStore(str(database)))
    with TestClient(create_app(service)) as client:
        response = client.post("/v1/documents/extract", json={"doc_id":"sample", "text":text("id_card")})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"doc_id", "doc_type", "status", "fields"}
        assert body["status"] == "ok"
        assert body["doc_type"] == "id_card"
        assert set(body["fields"]["id_number"]) == {"value", "confidence", "reason_codes"}
        assert body["fields"]["id_number"]["value"] == "XXXXXXXX" + ID[-4:]
        assert ID not in response.text
        bad = client.post("/v1/documents/extract", json={"doc_id":ID, "text":text("id_card"), "extra":ID})
        assert bad.status_code == 422
        assert ID not in bad.text
    assert ID not in caplog.text
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT attempts, response_json, source_sha256 FROM decisions").fetchall()
        assert len(rows) == 1 and rows[0][0] == 1
        assert "XXXXXXXX" + ID[-4:] in rows[0][1]
        assert len(rows[0][2]) == 64
    assert ID.encode() not in database.read_bytes()


def test_id_in_wrong_fields_and_doc_id_is_redacted():
    fields = dict(FIELDS["pan"], name=ID)
    result = Pipeline(StaticProvider(json.dumps({"fields":fields}))).extract(ID, text("pan", {"name":ID}))
    assert ID not in result.model_dump_json()


def test_audit_failure_cannot_auto_accept():
    class BrokenAudit:
        def record(self, *a, **k):
            raise RuntimeError(ID)
    result = Pipeline(FakeProvider(), audit=BrokenAudit()).extract("x", text("pan"))
    assert result.status == "failed" and result.error == "AUDIT_WRITE_FAILED"
    assert ID not in result.model_dump_json()


def test_evaluation_counts_denominators(tmp_path):
    generate(30, 42, tmp_path / "data")
    report = evaluate(tmp_path / "data")
    assert report["documents"] == 90
    assert report["noisy_documents"] == 36
    assert len(report["fields"]) == 14
    assert sum(s["total"] for s in report["fields"].values()) == 420
    assert sum(report["statuses"].values()) == 90
    assert report["calibrated"] is False
    assert "id_card.id_number" in report["fields"]
    assert report["fields"]["id_card.id_number"]["total"] == 30


def test_private_exact_id_fingerprint():
    import hashlib
    fingerprints = {}
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text("id_card"), _field_hashes=fingerprints)
    assert fingerprints["id_number"] == hashlib.sha256(ID.encode()).hexdigest()
    assert result.fields["id_number"].value == "XXXXXXXX" + ID[-4:]
    assert ID not in result.model_dump_json()


def test_retry_errors_never_echo_bad_values():
    class Recording(FakeProvider):
        def __init__(self):
            super().__init__(); self.prompts = []
        def extract(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return json.dumps({"fields":dict(FIELDS["pan"], **{ID:ID})})
    provider = Recording()
    result = Pipeline(provider).extract("x", text("pan"))
    assert result.status == "failed"
    assert ID not in result.model_dump_json()
    assert ID not in " ".join(provider.prompts)
    assert "payload:extra_forbidden" in provider.prompts[1]


def test_cli_json_and_persistent_audit(tmp_path, monkeypatch, capsys):
    import sys
    from kyc.extract import main
    source = tmp_path / "test.txt"
    source.write_text(text("id_card"))
    db = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(sys, "argv", ["kyc.extract", str(source), "--audit-db", str(db)])
    main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "ok"
    assert result["fields"]["id_number"]["value"] == "XXXXXXXX" + ID[-4:]
    assert ID not in output
    assert db.exists()


def test_api_failed_shape_and_review():
    with TestClient(create_app(Pipeline(FakeProvider(), today=TODAY))) as client:
        response = client.post("/v1/documents/extract", json={"doc_id":"unknown", "text":"SPECIMEN unknown document"})
        body = response.json()
        assert body["doc_type"] is None
        assert body["fields"] == {}
        assert body["status"] == "failed"
        assert body["error"] == "DOCUMENT_TYPE_UNKNOWN"
        response = client.post("/v1/documents/extract", json={"doc_id":"invalid", "text":text("id_card", {"pincode":"056001"})})
        assert response.json()["status"] == "needs_review"


@pytest.mark.parametrize("kind,changes,field", [
    ("pan", {"name":"SPECIMEN NEX0RA"}, "name"),
    ("address_proof", {"address":"12 M0DEL AVENUE"}, "address"),
])
def test_suspicious_text_requires_review(kind, changes, field):
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text(kind, changes))
    assert result.status == "needs_review"
    assert "TEXT_OCR_SUSPECT" in result.fields[field].reason_codes
    assert result.fields[field].confidence < 0.5


def test_source_supported_letter_substitution_is_not_identity_verification():
    # Deliberately records the prototype's limitation: OCR with no visible
    # formatting anomaly cannot be authenticated by local rules or source match.
    result = Pipeline(FakeProvider(), today=TODAY).extract("x", text("pan", {"name":"SPECIMEN NEXERA"}))
    assert result.status == "ok"
    assert result.fields["name"].confidence == 1.0


def test_regression_baseline_fixed_subset(tmp_path):
    """Guard field accuracy on a fixed-seed 30-document synthetic subset."""
    from kyc.eval import run_evaluation
    from kyc.config import THRESHOLD_CANDIDATES
    root = Path(__file__).resolve().parents[1]
    data = tmp_path / "regression-data"
    generate(10, 42, data)
    report = run_evaluation(data / "labels.jsonl", provider="fake", threshold_candidates=THRESHOLD_CANDIDATES)
    baseline = json.loads((root / "reports" / "baseline.json").read_text(encoding="utf-8"))
    assert report["documents"] == 30
    assert report["overall_field_accuracy"] >= baseline["overall_field_accuracy"]
