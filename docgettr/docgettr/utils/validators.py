import re
from datetime import datetime

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_REGEX = re.compile(r"^[2-9][0-9]{11}$")


def is_valid_pan(pan: str) -> bool:
    if not pan:
        return False
    return bool(PAN_REGEX.match(pan.strip().upper()))


def is_valid_aadhaar(aadhaar: str) -> bool:
    """Basic format check (Verhoeff checksum could be added later)."""
    if not aadhaar:
        return False
    digits = re.sub(r"\s+", "", aadhaar)
    return bool(AADHAAR_REGEX.match(digits))


def parse_indian_date(value):
    """Parse common Indian date formats. Returns ISO date string or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    value = str(value).strip()
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d %b %Y", "%d %B %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def render_filename_template(template: str, fields: dict) -> str:
    """Render a template like 'Aadhaar_{holder_name}_{aadhaar_number}'."""
    if not template:
        return ""
    out = template
    for k, v in (fields or {}).items():
        out = out.replace("{" + k + "}", str(v or "").strip())
    # Strip unfilled placeholders and clean separators
    out = re.sub(r"\{[^}]+\}", "", out)
    out = re.sub(r"[_\-\s]+", "_", out).strip("_")
    return out
