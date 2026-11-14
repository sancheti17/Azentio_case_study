"""Pure deterministic normalization and rules; ASCII identifiers only."""
import re
from datetime import date, datetime, timedelta, timezone

_D = (
    (0,1,2,3,4,5,6,7,8,9), (1,2,3,4,0,6,7,8,9,5),
    (2,3,4,0,1,7,8,9,5,6), (3,4,0,1,2,8,9,5,6,7),
    (4,0,1,2,3,9,5,6,7,8), (5,9,8,7,6,0,4,3,2,1),
    (6,5,9,8,7,1,0,4,3,2), (7,6,5,9,8,2,1,0,4,3),
    (8,7,6,5,9,3,2,1,0,4), (9,8,7,6,5,4,3,2,1,0),
)
_P = (
    (0,1,2,3,4,5,6,7,8,9), (1,5,7,6,2,8,3,0,9,4),
    (5,8,0,3,7,9,6,1,4,2), (8,9,1,6,0,4,3,5,2,7),
    (9,4,5,3,1,2,6,8,7,0), (4,2,8,6,5,7,3,9,0,1),
    (2,7,9,3,8,0,6,4,1,5), (7,0,4,6,9,1,3,2,5,8),
)
_INV = (0,4,3,2,1,5,6,7,8,9)
_DIGITS = str.maketrans({"O":"0", "I":"1", "o":"0", "l":"1", "i":"1"})
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def today_ist() -> date:
    # Modern IST has a fixed UTC+05:30 offset; no system tzdata required.
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


def verhoeff_valid(value: str) -> bool:
    if not re.fullmatch(r"[0-9]+", value):
        return False
    c = 0
    for i, digit in enumerate(reversed(value)):
        c = _D[c][_P[i % 8][int(digit)]]
    return c == 0


def verhoeff_digit(prefix: str) -> str:
    if not re.fullmatch(r"[0-9]+", prefix):
        raise ValueError("prefix must be ASCII digits")
    c = 0
    for i, digit in enumerate(reversed(prefix)):
        c = _D[c][_P[(i + 1) % 8][int(digit)]]
    return str(_INV[c])


def pan_valid(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3}P[A-Z][0-9]{4}[A-Z]", value))


def normalize_date(value: str) -> str:
    """Full dates only. Slash and numeric-dash dates are always day-first."""
    text = value.strip()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return date.fromisoformat(text).isoformat()
    match = re.fullmatch(r"([0-9]{2})[/-]([0-9]{2})[/-]([0-9]{4})", text)
    if match:
        d, m, y = map(int, match.groups())
        return date(y, m, d).isoformat()
    match = re.fullmatch(r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})", text)
    if match and match[2].lower() in _MONTHS:
        return date(int(match[3]), _MONTHS[match[2].lower()], int(match[1])).isoformat()
    raise ValueError("unsupported or invalid full date")


def normalize(field: str, raw: str) -> tuple[str, bool]:
    """Return canonical value and whether confusable-character repair occurred."""
    v = " ".join(raw.split())
    corrected = False
    if field in {"id_number", "pincode"}:
        v = re.sub(r"[\s-]", "", v)
        fixed = v.translate(_DIGITS)
        corrected = fixed != v
        v = fixed
    elif field == "pan_number":
        v = re.sub(r"\s", "", v).upper()
        if len(v) == 10:
            fixed = (v[:5].replace("0", "O").replace("1", "I")
                     + v[5:9].translate(_DIGITS)
                     + v[9:].replace("0", "O").replace("1", "I"))
            corrected = fixed != v
            v = fixed
    elif field in {"dob", "issue_date"}:
        # Repair only numeric positions, never month-name letters.
        fixed = v
        if re.fullmatch(r"[0-9OIlio/\-]+", v):
            fixed = v.translate(_DIGITS)
        else:
            parts = v.split("-")
            if len(parts) == 3:
                fixed = "-".join([parts[0].translate(_DIGITS), parts[1], parts[2].translate(_DIGITS)])
        corrected = fixed != v
        v = fixed
        try:
            v = normalize_date(v)
        except (ValueError, OverflowError):
            pass
    elif field == "gender":
        v = {"M":"MALE", "F":"FEMALE", "OTHER":"OTHER"}.get(v.upper(), v.upper())
    else:
        v = v.upper()
    return v, corrected


def rule_reasons(field: str, value: str, today: date) -> list[str]:
    # Generic conservative signals, not repairs or generator-specific tokens.
    # Digits inside names and letter-digit-letter runs in addresses may be OCR
    # mistakes; legitimate alphanumeric addresses can be manually confirmed.
    if field in {"name", "father_name"} and re.search(r"[0-9]", value):
        return ["TEXT_OCR_SUSPECT"]
    if field == "address" and re.search(r"[A-Z][01][A-Z]", value):
        return ["TEXT_OCR_SUSPECT"]
    if field == "pan_number" and not pan_valid(value):
        return ["PAN_FORMAT_INVALID"]
    if field == "id_number" and (not re.fullmatch(r"[0-9]{12}", value) or not verhoeff_valid(value)):
        return ["ID_CHECKSUM_FAILED"]
    if field == "pincode" and not re.fullmatch(r"[1-9][0-9]{5}", value):
        return ["PINCODE_INVALID"]
    if field in {"dob", "issue_date"}:
        try:
            parsed = date.fromisoformat(normalize_date(value))
        except (ValueError, OverflowError):
            return ["DATE_INVALID"]
        if parsed > today:
            return ["DATE_INVALID"]
    if field == "gender" and value not in {"MALE", "FEMALE", "OTHER"}:
        return ["GENDER_INVALID"]
    return []
