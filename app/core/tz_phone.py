"""Tanzania mobile number normalization (+255 6/7 XX XXX XXX)."""

from __future__ import annotations

import re

TZ_MOBILE_NATIONAL = re.compile(r"^[67]\d{8}$")


def normalize_tz_mobile_e164(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("255"):
        digits = digits[3:]
    elif digits.startswith("0") and len(digits) >= 10:
        digits = digits[1:]
    if len(digits) > 9:
        digits = digits[-9:]
    if not TZ_MOBILE_NATIONAL.match(digits):
        return None
    return f"+255{digits}"
