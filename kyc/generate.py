"""Reproducible synthetic KYC data; never derives identities from real records."""
import argparse
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from .config import EVALUATION_AS_OF
from .rules import verhoeff_digit

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
SCENARIOS = {
    0: ["NAME_MISMATCH"],
    1: ["DOB_MISMATCH"],
    2: ["PAN_SURNAME_INITIAL_MISMATCH"],
    3: ["ADDRESS_PROOF_STALE"],
    4: ["NAME_MISMATCH", "DOB_MISMATCH"],
    5: ["NAME_MISMATCH", "PAN_SURNAME_INITIAL_MISMATCH"],
    6: ["DOB_MISMATCH", "ADDRESS_PROOF_STALE"],
    7: ["PAN_SURNAME_INITIAL_MISMATCH", "ADDRESS_PROOF_STALE"],
}


def _letters(number: int) -> str:
    chars = []
    while True:
        chars.append(chr(65 + number % 26))
        number //= 26
        if not number:
            return "".join(reversed(chars))


def _display_date(value: str, style: int) -> str:
    d = date.fromisoformat(value)
    if style % 3 == 0:
        return value
    if style % 3 == 1:
        return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    return f"{d.day:02d}-{MONTHS[d.month-1]}-{d.year:04d}"


def generate(applicants: int, seed: int, out: str | Path) -> list[dict]:
    if applicants < 1:
        raise ValueError("applicants must be positive")
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError("Output directory must be empty")
    rng = random.Random(seed)
    as_of = date.fromisoformat(EVALUATION_AS_OF)
    n_docs = 3 * applicants
    noisy_indices = set(rng.sample(range(n_docs), math.ceil(n_docs * 0.4)))
    rows, applicant_rows = [], []
    for i in range(applicants):
        suffix = _letters(i)
        # All names and document values are conspicuously synthetic.
        name = f"SPECIMEN NEXORA {suffix}"
        father = f"SPECIMEN PARENT {suffix}"
        dob = (date(1965, 1, 1) + timedelta(days=rng.randrange(15000))).isoformat()
        # 4th character is P; 5th character matches the final name token initial.
        pan = "SYNP" + suffix[0] + f"{rng.randrange(10000):04d}" + chr(65 + i % 26)
        prefix = str(rng.randrange(2)) + f"{rng.randrange(10**10):010d}"
        id_number = prefix + verhoeff_digit(prefix)
        address = f"{i+1} MODEL AVENUE, TEST NAGAR, SYNTHETIC CITY"
        pincode = str(rng.randrange(100000, 1000000))
        # Clean evidence is current as of the fixed date; seeded stale cases are changed below.
        issue_date = (as_of - timedelta(days=rng.randrange(0, 91))).isoformat()
        documents = {
            "pan": {"name":name, "father_name":father, "dob":dob, "pan_number":pan},
            "id_card": {"name":name, "dob":dob, "gender":rng.choice(["MALE", "FEMALE", "OTHER"]),
                        "id_number":id_number, "address":address, "pincode":pincode},
            "address_proof": {"name":name, "address":address, "pincode":pincode, "issue_date":issue_date},
        }
        expected = list(SCENARIOS.get(i, []))
        if "NAME_MISMATCH" in expected:
            documents["address_proof"]["name"] = f"SPECIMEN ALTERED {suffix}"
        if "DOB_MISMATCH" in expected:
            documents["id_card"]["dob"] = (date.fromisoformat(dob) + timedelta(days=1)).isoformat()
        if "PAN_SURNAME_INITIAL_MISMATCH" in expected:
            wrong = "Z" if pan[4] != "Z" else "Y"
            documents["pan"]["pan_number"] = pan[:4] + wrong + pan[5:]
        if "ADDRESS_PROOF_STALE" in expected:
            documents["address_proof"]["issue_date"] = (as_of - timedelta(days=91 + i)).isoformat()
        applicant_rows.append({"applicant_id":f"app_{i+1:04d}", "as_of":EVALUATION_AS_OF,
                               "expected_reason_codes":sorted(expected)})
        for kind, truth in documents.items():
            index = len(rows)
            doc_id = f"app_{i+1:04d}_{kind}"
            header = {"pan":"INCOME TAX DEPARTMENT - PAN CARD", "id_card":"GOVERNMENT ID - AADHAAR STYLE",
                      "address_proof":rng.choice(["UTILITY BILL - ADDRESS PROOF", "BANK STATEMENT - ADDRESS PROOF"])}[kind]
            visible = dict(truth)
            for key in ("dob", "issue_date"):
                if key in visible:
                    visible[key] = _display_date(visible[key], index)
            noise_types = []
            if index in noisy_indices:
                target = "pan_number" if kind == "pan" else "id_number" if kind == "id_card" else "pincode"
                old = visible[target]
                positions = [j for j, c in enumerate(old) if c in "01OI"]
                if positions:
                    pos = positions[0]
                    visible[target] = old[:pos] + {"0":"O", "1":"I", "O":"0", "I":"1"}[old[pos]] + old[pos+1:]
                noise_types.append("confusable_swap")
                noise_types.append("line_shuffle")
                noise_types.append("header_footer")
                noise_types.append("mixed_date_format")
                if index % 7 == 0:
                    visible["name"] = visible["name"].replace("NEXORA", "NEX0RA")
                    noise_types.append("text_confusable")
            lines = [key.replace("_", " ").title() + ": " + value for key, value in visible.items() if key in truth]
            if noise_types:
                rng.shuffle(lines)
                lines = ["SCAN COPY - NOT A VALID IDENTITY DOCUMENT"] + lines + ["OCR END / PAGE ONE"]
            content = "SPECIMEN - SYNTHETIC TEST DATA ONLY\n" + header + "\n" + "\n".join(lines) + "\n"
            (destination / f"{doc_id}.txt").write_bytes(content.encode("utf-8"))
            rows.append({"doc_id":doc_id, "applicant_id":f"app_{i+1:04d}", "doc_type":kind,
                         "fields":truth, "noisy":bool(noise_types), "noise_types":noise_types})
    labels_payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows)
    applicants_payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in applicant_rows)
    (destination / "labels.jsonl").write_bytes(labels_payload.encode("utf-8"))
    (destination / "applicants.jsonl").write_bytes(applicants_payload.encode("utf-8"))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applicants", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data")
    args = parser.parse_args()
    try:
        rows = generate(args.applicants, args.seed, args.out)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(json.dumps({"documents":len(rows), "noisy_documents":sum(r["noisy"] for r in rows)}))


if __name__ == "__main__":
    main()
