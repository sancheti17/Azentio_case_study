"""Synthetic smoke evaluation, not calibration or evidence of real-world safety."""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from .pipeline import Pipeline
from .providers import FakeProvider
from .rules import normalize


def evaluate(data: str | Path, threshold: float = 0.8) -> dict:
    root = Path(data)
    rows = [json.loads(line) for line in (root / "labels.jsonl").read_text().splitlines() if line]
    counts = {}
    statuses = Counter()
    errors = 0
    escaped_fields = 0
    escaped_docs = 0
    false_review = 0
    for row in rows:
        fingerprints: dict[str, str] = {}
        result = Pipeline(FakeProvider(), threshold=threshold, today=date(2026, 1, 1)).extract(
            row["doc_id"], (root / (row["doc_id"] + ".txt")).read_text(), _field_hashes=fingerprints)
        statuses[result.status] += 1
        doc_errors = 0
        for key, expected in row["fields"].items():
            expected, _ = normalize(key, expected)
            # Compare canonical full-value hashes from this exact pipeline run.
            # Neither identifiers nor hashes are written to evaluation reports.
            metric_key = row["doc_type"] + "." + key
            expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
            correct = fingerprints.get(key) == expected_hash
            stats = counts.setdefault(metric_key, {"correct":0, "total":0, "accepted_errors":0, "accepted_total":0})
            stats["total"] += 1
            stats["accepted_total"] += int(result.status == "ok")
            stats["correct"] += int(correct)
            if not correct:
                errors += 1
                doc_errors += 1
                if result.status == "ok":
                    escaped_fields += 1
                    stats["accepted_errors"] += 1
        if result.status == "ok" and doc_errors:
            escaped_docs += 1
        if result.status == "needs_review" and doc_errors == 0:
            false_review += 1
    for stats in counts.values():
        stats["accuracy"] = round(stats["correct"] / stats["total"], 6)
    accepted = statuses["ok"]
    # The accepted denominator must exclude all reviewed and failed documents.
    accepted_fields = sum(s["accepted_total"] for s in counts.values())
    # One-sided 95% upper bound only for zero observed errors. It is not valid
    # to infer population risk from this shared-template synthetic sample.
    zero_error_upper = (1 - math.pow(0.05, 1 / accepted)) if accepted and escaped_docs == 0 else None
    return {"provider":"FakeProvider", "threshold":threshold, "calibrated":False,
            "decision_date":"2026-01-01", "documents":len(rows),
            "applicants":len({r["applicant_id"] for r in rows}),
            "noisy_documents":sum(r.get("noisy", False) for r in rows),
            "statuses":dict(statuses), "fields":counts, "all_field_errors":errors,
            "accepted_fields":accepted_fields,
            "accepted_field_error_rate":escaped_fields / accepted_fields if accepted_fields else None,
            "accepted_field_errors":escaped_fields, "accepted_document_errors":escaped_docs,
            "accepted_document_error_rate":escaped_docs / accepted if accepted else None,
            "reviewed_documents_without_observed_field_errors":false_review,
            "zero_error_one_sided_95pct_upper_bound":zero_error_upper,
            "limitations":["Same-template synthetic smoke test; not held-out calibration or bank approval evidence.",
                           "Full IDs compared via private in-memory hashes; reports contain aggregate counts only.",
                           "Document ok means extraction gating, never verified identity or credit approval."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = evaluate(args.data, args.threshold)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
