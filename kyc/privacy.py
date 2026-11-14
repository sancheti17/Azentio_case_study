import re


def mask_id(value: str) -> str:
    """Only canonical ASCII digits may expose the final four; anything else hides all."""
    if re.fullmatch(r"[0-9]{12}", value):
        return "XXXXXXXX" + value[-4:]
    return "XXXXXXXXXXXX"


def redact(text: str) -> str:
    # Defence in depth for caller-controlled doc_id and non-ID fields. Capture
    # spaced, hyphenated, OCR-confusable and longer digit runs conservatively.
    return re.sub(r"(?<![0-9OIlio])(?:[0-9OIlio][ -]?){11,}[0-9OIlio](?![0-9OIlio])", "[REDACTED_ID]", text)
