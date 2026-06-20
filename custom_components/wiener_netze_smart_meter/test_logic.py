from datetime import datetime, timezone

from logic import (
    bucket_hourly,
    compute_hourly_cost,
    latest_daily_reading,
    parse_price_data,
)


def _utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


class StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_daily_values(self, zaehlpunkt, von, bis):
        self.calls.append((zaehlpunkt, von, bis))
        return self.payload


def test_returns_latest_messwert():
    client = StubClient(
        {
            "zaehlwerke": [
                {
                    "messwerte": [
                        {"messwert": 100, "zeitBis": "2026-06-17T22:00:00.000Z"},
                        {"messwert": 200, "zeitBis": "2026-06-18T22:00:00.000Z"},
                    ]
                }
            ]
        }
    )
    reading = latest_daily_reading(client, "AT001", now=datetime(2026, 6, 19))
    assert reading.daily_wh == 200
    assert reading.reading_date == "2026-06-18"
    assert reading.zaehlpunkt == "AT001"


def test_returns_none_when_no_data():
    assert latest_daily_reading(StubClient(None), "AT001") is None
    assert latest_daily_reading(StubClient({"zaehlwerke": []}), "AT001") is None
    assert (
        latest_daily_reading(StubClient({"zaehlwerke": [{"messwerte": []}]}), "AT001")
        is None
    )


def test_uses_lookback_window():
    client = StubClient({"zaehlwerke": [{"messwerte": []}]})
    latest_daily_reading(client, "AT001", now=datetime(2026, 6, 19))
    zaehlpunkt, von, bis = client.calls[0]
    assert (zaehlpunkt, von, bis) == ("AT001", "2026-06-14", "2026-06-19")


def test_bucket_hourly_sums_quarters_into_hours():
    messwerte = [
        {"messwert": 10, "zeitVon": "2026-06-18T08:00:00.000Z"},
        {"messwert": 20, "zeitVon": "2026-06-18T08:15:00.000Z"},
        {"messwert": 30, "zeitVon": "2026-06-18T08:30:00.000Z"},
        {"messwert": 40, "zeitVon": "2026-06-18T08:45:00.000Z"},
        {"messwert": 5, "zeitVon": "2026-06-18T09:00:00.000Z"},
    ]
    buckets = bucket_hourly(messwerte)
    assert buckets == [
        (datetime(2026, 6, 18, 8, tzinfo=timezone.utc), 100),
        (datetime(2026, 6, 18, 9, tzinfo=timezone.utc), 5),
    ]


def test_bucket_hourly_empty():
    assert bucket_hourly([]) == []


def test_parse_price_data_to_utc_hours():
    data = [
        {"start_time": "2026-06-19T00:00:00+02:00", "end_time": "x", "price_per_kwh": 0.315624},
        {"start_time": "2026-06-19T01:00:00+02:00", "end_time": "x", "price_per_kwh": 0.30083},
    ]
    prices = parse_price_data(data)
    # 00:00 +02:00 == 22:00 UTC previous day
    assert prices[_utc(2026, 6, 18, 22)] == 0.315624
    assert prices[_utc(2026, 6, 18, 23)] == 0.30083


def test_compute_hourly_cost_multiplies_and_accumulates():
    energy = [(_utc(2026, 6, 18, 8), 1000.0), (_utc(2026, 6, 18, 9), 500.0)]
    prices = {_utc(2026, 6, 18, 8): 0.30, _utc(2026, 6, 18, 9): 0.40}
    result = compute_hourly_cost(energy, prices)
    # 1000 Wh = 1 kWh * 0.30 = 0.30 ; 500 Wh = 0.5 kWh * 0.40 = 0.20
    assert result[0] == (_utc(2026, 6, 18, 8), 0.30, 0.30)
    assert round(result[1][1], 4) == 0.20
    assert round(result[1][2], 4) == 0.50


def test_compute_hourly_cost_skips_unpriced_and_respects_start_after():
    energy = [(_utc(2026, 6, 18, 8), 1000.0), (_utc(2026, 6, 18, 9), 1000.0)]
    prices = {_utc(2026, 6, 18, 9): 0.40}  # hour 8 has no price
    result = compute_hourly_cost(
        energy, prices, start_after=_utc(2026, 6, 18, 7), starting_total=5.0
    )
    assert len(result) == 1
    assert result[0] == (_utc(2026, 6, 18, 9), 0.40, 5.40)

    # start_after excludes hour 9
    assert compute_hourly_cost(energy, prices, start_after=_utc(2026, 6, 18, 9)) == []


if __name__ == "__main__":
    test_returns_latest_messwert()
    test_returns_none_when_no_data()
    test_uses_lookback_window()
    test_bucket_hourly_sums_quarters_into_hours()
    test_bucket_hourly_empty()
    test_parse_price_data_to_utc_hours()
    test_compute_hourly_cost_multiplies_and_accumulates()
    test_compute_hourly_cost_skips_unpriced_and_respects_start_after()
    print("ok")
