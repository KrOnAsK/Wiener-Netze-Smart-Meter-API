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


def parse_price_data(data: list[dict]) -> dict[datetime, float]:
    """Map hour-start (UTC) -> price_per_kwh from an EPEX Spot 'data' attribute."""
    prices: dict[datetime, float] = {}
    for entry in data or []:
        start = datetime.fromisoformat(entry["start_time"]).astimezone(timezone.utc)
        hour = start.replace(minute=0, second=0, microsecond=0)
        prices[hour] = float(entry["price_per_kwh"])
    return prices


def compute_hourly_cost(
    energy_buckets: list[tuple[datetime, float]],
    price_map: dict[datetime, float],
    *,
    start_after: datetime | None = None,
    starting_total: float = 0.0,
) -> list[tuple[datetime, float, float]]:
    """Return (hour_utc, hour_cost, cumulative_cost) for each priced energy hour.

    energy_buckets are (hour_utc, wh); price_map is {hour_utc: currency_per_kwh}.
    Hours at or before start_after, or with no known price, are skipped.
    """
    total = starting_total
    out: list[tuple[datetime, float, float]] = []
    for hour, wh in energy_buckets:
        if start_after is not None and hour <= start_after:
            continue
        price = price_map.get(hour)
        if price is None:
            # ponytail: no retro-fill of skipped hours; EPEX prices are known
            # day-ahead so a priced hour is essentially always available by the
            # time the (1-2 day lagged) energy arrives.
            continue
        cost = (wh / 1000.0) * price
        total += cost
        out.append((hour, cost, total))
    return out
