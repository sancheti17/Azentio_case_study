"""Evidence lexicon independent of the synthetic generator and its labels."""
import re
from .models import SCHEMAS

LABELS = {
    "name": {"name", "full name", "customer name", "account holder"},
    "father_name": {"father name", "father's name", "father"},
    "dob": {"dob", "date of birth"},
    "pan_number": {"pan", "pan number", "permanent account number"},
    "gender": {"gender", "sex"},
    "id_number": {"id number", "aadhaar", "aadhaar number", "uid"},
    "address": {"address", "residential address", "service address"},
    "pincode": {"pincode", "pin code", "postal code"},
    "issue_date": {"issue date", "statement date", "bill date"},
}


def source_fields(text: str, doc_type: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        pair = re.split(r"\s*[:=]\s*", line.strip(), maxsplit=1)
        if len(pair) != 2:
            continue
        label, value = pair
        for field in SCHEMAS[doc_type].model_fields:
            if label.lower() in LABELS[field] and value.strip():
                result.setdefault(field, []).append(value.strip())
    return result


def classify(text: str) -> tuple[str | None, str | None]:
    upper = text.upper()
    patterns = {
        "pan": r"\bPAN(?: CARD| NUMBER)?\b|PERMANENT ACCOUNT NUMBER|INCOME TAX DEPARTMENT",
        "id_card": r"AADHAAR|GOVERNMENT ID|\bUID\b",
        "address_proof": r"UTILITY BILL|BANK STATEMENT|ADDRESS PROOF",
    }
    hits = [kind for kind, pattern in patterns.items() if re.search(pattern, upper)]
    if not hits:
        return None, "DOCUMENT_TYPE_UNKNOWN"
    if len(hits) != 1:
        return None, "DOCUMENT_TYPE_AMBIGUOUS"
    return hits[0], None
