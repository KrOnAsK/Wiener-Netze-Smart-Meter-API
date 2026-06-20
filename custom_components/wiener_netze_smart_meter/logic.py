from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LOOKBACK_DAYS = 5


@dataclass
class MeterReading:
    zaehlpunkt: str
    daily_wh: float
    reading_date: str


def latest_daily_reading(client, zaehlpunkt: str, *, now: datetime | None = None) -> MeterReading | None:
    now = now or datetime.now()
    von = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    bis = now.strftime("%Y-%m-%d")
    data = client.get_daily_values(zaehlpunkt, von, bis)
    if not data:
        return None

    messwerte = (data.get("zaehlwerke") or [{}])[0].get("messwerte") or []
    if not messwerte:
        return None

    latest = messwerte[-1]
    return MeterReading(
        zaehlpunkt=zaehlpunkt,
        daily_wh=latest["messwert"],
        reading_date=latest["zeitBis"][:10],
    )


def quarter_hour_messwerte(client, zaehlpunkt: str, von: str, bis: str) -> list[dict]:
    data = client.get_quarter_hour_values(zaehlpunkt, von, bis)
    if not data:
        return []
    return (data.get("zaehlwerke") or [{}])[0].get("messwerte") or []


def bucket_hourly(messwerte: list[dict]) -> list[tuple[datetime, float]]:
    """Sum quarter-hour Wh values into (hour_start_utc, wh) buckets, sorted by time."""
    buckets: dict[datetime, float] = defaultdict(float)
    for m in messwerte:
        start = datetime.strptime(m["zeitVon"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
        hour = start.replace(minute=0, second=0, microsecond=0)
        buckets[hour] += m["messwert"]
    return sorted(buckets.items())
