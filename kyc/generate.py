"""Reproducible synthetic data; never derives IDs or names from real records."""
import argparse
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from .rules import verhoeff_digit

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


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
    # Never silently overwrite existing work, nor leave stale documents.
    if any(destination.iterdir()):
        raise FileExistsError("Output directory must be empty")
    rng = random.Random(seed)
    n_docs = 3 * applicants
    noisy_indices = set(rng.sample(range(n_docs), math.ceil(n_docs * 0.4)))
    rows = []
    for i in range(applicants):
        suffix = _letters(i)
        # Artificial tokens, not plausible real people's identities.
        name = f"SPECIMEN NEXORA {suffix}"
        father = f"SPECIMEN PARENT {suffix}"
        dob = (date(1965, 1, 1) + timedelta(days=rng.randrange(15000))).isoformat()
        pan = "SYNPT" + f"{rng.randrange(10000):04d}" + chr(65 + i % 26)
        prefix = str(rng.randrange(2)) + f"{rng.randrange(10**10):010d}"
        id_number = prefix + verhoeff_digit(prefix)
        address = f"{i+1} MODEL AVENUE, TEST NAGAR, SYNTHETIC CITY"
        pincode = str(rng.randrange(100000, 1000000))
        issue_date = (date(2024, 1, 1) + timedelta(days=rng.randrange(300))).isoformat()
        documents = {
            "pan": {"name":name, "father_name":father, "dob":dob, "pan_number":pan},
            "id_card": {"name":name, "dob":dob, "gender":rng.choice(["MALE", "FEMALE", "OTHER"]),
                        "id_number":id_number, "address":address, "pincode":pincode},
            "address_proof": {"name":name, "address":address, "pincode":pincode, "issue_date":issue_date},
        }
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
                # A genuine O/0 or I/1 corruption on every noisy document.
                target = "pan_number" if kind == "pan" else "id_number" if kind == "id_card" else "pincode"
                old = visible[target]
                positions = [j for j, c in enumerate(old) if c in "01OI"]
                if positions:
                    j = rng.choice(positions)
                    c = {"0":"O", "1":"I", "O":"0", "I":"1"}[old[j]]
                    visible[target] = old[:j] + c + old[j+1:]
                else:
                    # Preserve truth; apply a text-field confusable when no digit eligible.
                    visible["address"] = visible.get("address", address).replace("MODEL", "M0DEL")
                noise_types = ["confusable_swap", "line_shuffle", "header_footer", "mixed_date_format"]
                # Unrecoverable-but-plausible OCR corruption illustrates why a
                # source match is not proof of correctness (evaluation measures it).
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
    payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows)
    (destination / "labels.jsonl").write_bytes(payload.encode("utf-8"))
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
