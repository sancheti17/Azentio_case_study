import argparse
import json
from pathlib import Path
from .audit import AuditStore
from .config import DEFAULT_REVIEW_THRESHOLD
from .models import ExtractRequest
from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Extract a synthetic OCR document; all ID results are masked")
    parser.add_argument("path", type=Path)
    parser.add_argument("--threshold", type=float, default=DEFAULT_REVIEW_THRESHOLD)
    parser.add_argument("--audit-db", default="kyc-audit.sqlite3")
    args = parser.parse_args()
    try:
        request = ExtractRequest(doc_id=args.path.stem, text=args.path.read_text(encoding="utf-8"))
        result = Pipeline(threshold=args.threshold, audit=AuditStore(args.audit_db)).extract(request.doc_id, request.text)
    except Exception:
        # Avoid file names, source content or raw exceptions in terminal logs.
        print(json.dumps({"doc_id":"unavailable", "doc_type":None, "status":"failed", "fields":{},
                          "error":"CLI_INPUT_OR_CONFIGURATION_ERROR"}))
        raise SystemExit(2)
    print(result.model_dump_json(exclude={"error"} if result.error is None else set()))
    if result.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
