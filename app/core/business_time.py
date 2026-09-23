"""Calendar boundaries for Tanzania retail tenants (Africa/Dar_es_Salaam)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Dar_es_Salaam")


def now_local() -> datetime:
    return datetime.now(TZ)


def period_start_local(range_key: str, ref: datetime | None = None) -> datetime | None:
    """Start of month/quarter/year in local TZ, or None for all-time."""
    now = ref or now_local()
    if range_key == "all":
        return None
    if range_key == "year":
        return datetime(now.year, 1, 1, tzinfo=TZ)
    if range_key == "quarter":
        q_month = ((now.month - 1) // 3) * 3 + 1
        return datetime(now.year, q_month, 1, tzinfo=TZ)
    return datetime(now.year, now.month, 1, tzinfo=TZ)
