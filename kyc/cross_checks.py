"""Pure cross-document consistency checks over already-extracted fields."""
from datetime import date, timedelta
from difflib import SequenceMatcher
import re
import unicodedata

NAME_SIMILARITY_THRESHOLD = 0.80
ADDRESS_PROOF_STALE_DAYS = 90


def _value(fields, key):
    value = (fields or {}).get(key)
    if isinstance(value, dict):
        value = value.get("value")
    elif hasattr(value, "value"):
        value = value.value
    return value if isinstance(value, str) else ""


def _name_tokens(value):
    text = unicodedata.normalize("NFKD", str(value or "")).upper()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.findall(r"[A-Z0-9]+", text)


def _token_score(a, b):
    if a == b:
        return 1.0
    # A single-character token is a conventional initial (e.g. A. vs ANITA).
    if len(a) == 1 and b.startswith(a) or len(b) == 1 and a.startswith(b):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def name_similarity(left, right):
    """Order-independent token score; initials are accepted, threshold is 0.80."""
    a, b = _name_tokens(left), _name_tokens(right)
    if not a or not b:
        return 0.0
    scores = [[_token_score(x, y) for y in b] for x in a]
    # Names are short. Exact maximum-weight matching avoids order-sensitive greed.
    memo = {}
    def best(i, used):
        key = (i, used)
        if key in memo:
            return memo[key]
        if i == len(a):
            return 0.0
        result = best(i + 1, used)
        for j, score in enumerate(scores[i]):
            if not (used & (1 << j)) and score >= NAME_SIMILARITY_THRESHOLD:
                result = max(result, score + best(i + 1, used | (1 << j)))
        memo[key] = result
        return result
    return best(0, 0) / max(len(a), len(b))


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def cross_document_reason_codes(fields_by_type, as_of):
    """Return sorted applicant-level reason codes for PAN, ID, and address proof.

    ``fields_by_type`` maps document types to plain field-value mappings (or
    FieldResult-like values). Missing/unparseable values are handled by the
    document field validation path and are skipped here.
    """
    fields_by_type = fields_by_type or {}
    codes = set()
    names = [_value(fields_by_type.get(kind, {}), "name") for kind in ("pan", "id_card", "address_proof")]
    present_names = [name for name in names if name.strip()]
    if len(present_names) >= 2 and any(
        name_similarity(present_names[i], present_names[j]) < NAME_SIMILARITY_THRESHOLD
        for i in range(len(present_names)) for j in range(i + 1, len(present_names))
    ):
        codes.add("NAME_MISMATCH")

    pan_dob = _as_date(_value(fields_by_type.get("pan", {}), "dob"))
    id_dob = _as_date(_value(fields_by_type.get("id_card", {}), "dob"))
    if pan_dob and id_dob and pan_dob != id_dob:
        codes.add("DOB_MISMATCH")

    pan_number = re.sub(r"\s+", "", _value(fields_by_type.get("pan", {}), "pan_number")).upper()
    last_tokens = _name_tokens(_value(fields_by_type.get("pan", {}), "name"))
    if len(pan_number) >= 5 and last_tokens and pan_number[4].isalpha():
        if pan_number[4] != last_tokens[-1][0]:
            codes.add("PAN_SURNAME_INITIAL_MISMATCH")

    proof_date = _as_date(_value(fields_by_type.get("address_proof", {}), "issue_date"))
    reference_date = _as_date(as_of)
    if proof_date and reference_date and (reference_date - proof_date).days > ADDRESS_PROOF_STALE_DAYS:
        codes.add("ADDRESS_PROOF_STALE")
    return sorted(codes)
