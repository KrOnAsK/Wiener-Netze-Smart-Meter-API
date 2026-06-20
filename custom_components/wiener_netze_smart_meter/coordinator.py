from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from wiener_netze_smart_meter_api import WNAPIClient
from wiener_netze_smart_meter_api.exceptions import WNAPIAuthenticationError

from .const import (
    BACKFILL_DAYS,
    CONF_PRICE_ENTITY,
    COST_CURRENCY,
    DOMAIN,
    UPDATE_INTERVAL_HOURS,
)
from .logic import (
    MeterReading,
    bucket_hourly,
    compute_hourly_cost,
    latest_daily_reading,
    parse_price_data,
    quarter_hour_messwerte,
)

_LOGGER = logging.getLogger(__name__)


class WNSmartMeterCoordinator(DataUpdateCoordinator[dict[str, MeterReading]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: WNAPIClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> dict[str, MeterReading]:
        try:
            readings = await self.hass.async_add_executor_job(self._fetch)
        except WNAPIAuthenticationError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err

        for zaehlpunkt in readings:
            await self._import_hourly_statistics(zaehlpunkt)
            await self._import_cost_statistics(zaehlpunkt)
        return readings

    def _fetch(self) -> dict[str, MeterReading]:
        anlagen = self.client.get_anlagendaten()
        if isinstance(anlagen, dict):
            anlagen = [anlagen]

        readings: dict[str, MeterReading] = {}
        for anlage in anlagen or []:
            zaehlpunkt = anlage.get("zaehlpunktnummer")
            if not zaehlpunkt:
                continue
            reading = latest_daily_reading(self.client, zaehlpunkt)
            if reading:
                readings[zaehlpunkt] = reading
        return readings

    async def _import_hourly_statistics(self, zaehlpunkt: str) -> None:
        statistic_id = f"{DOMAIN}:{zaehlpunkt.lower()}_hourly_energy"

        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        if last.get(statistic_id):
            total = last[statistic_id][0]["sum"]
            start_after = datetime.fromtimestamp(
                last[statistic_id][0]["start"], tz=timezone.utc
            )
            von = start_after.strftime("%Y-%m-%d")
        else:
            total = 0.0
            start_after = None
            von = (datetime.now() - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d")
        bis = datetime.now().strftime("%Y-%m-%d")

        messwerte = await self.hass.async_add_executor_job(
            quarter_hour_messwerte, self.client, zaehlpunkt, von, bis
        )

        statistics: list[StatisticData] = []
        for start, wh in bucket_hourly(messwerte):
            if start_after is not None and start <= start_after:
                continue
            total += wh
            statistics.append(StatisticData(start=start, state=wh, sum=total))

        if not statistics:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Smart meter {zaehlpunkt[-6:]} hourly energy",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        )
        async_add_external_statistics(self.hass, metadata, statistics)

    async def _import_cost_statistics(self, zaehlpunkt: str) -> None:
        price_entity = self.entry.options.get(CONF_PRICE_ENTITY)
        if not price_entity:
            return

        statistic_id = f"{DOMAIN}:{zaehlpunkt.lower()}_hourly_cost"
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        if last.get(statistic_id):
            total = last[statistic_id][0]["sum"]
            start_after = datetime.fromtimestamp(
                last[statistic_id][0]["start"], tz=timezone.utc
            )
            window_start = start_after
        else:
            total = 0.0
            start_after = None
            window_start = datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
        von = window_start.strftime("%Y-%m-%d")
        bis = datetime.now().strftime("%Y-%m-%d")

        messwerte = await self.hass.async_add_executor_job(
            quarter_hour_messwerte, self.client, zaehlpunkt, von, bis
        )
        energy_buckets = bucket_hourly(messwerte)
        price_map = await self._build_price_map(
            price_entity, window_start, datetime.now(timezone.utc)
        )

        rows = compute_hourly_cost(
            energy_buckets, price_map, start_after=start_after, starting_total=total
        )
        if not rows:
            return

        statistics = [StatisticData(start=h, state=c, sum=s) for h, c, s in rows]
        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Smart meter {zaehlpunkt[-6:]} hourly cost",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=COST_CURRENCY,
        )
        async_add_external_statistics(self.hass, metadata, statistics)

    async def _build_price_map(
        self, price_entity: str, start_dt: datetime, end_dt: datetime
    ) -> dict[datetime, float]:
        """Hour-start (UTC) -> price/kWh, from the price entity's hourly stats,
        overlaid with its live forecast attribute for the most recent hours."""
        prices: dict[datetime, float] = {}

        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start_dt,
            end_dt,
            {price_entity},
            "hour",
            None,
            {"mean"},
        )
        for row in stats.get(price_entity, []):
            if row.get("mean") is None:
                continue
            raw = row["start"]
            start = (
                raw
                if isinstance(raw, datetime)
                else datetime.fromtimestamp(raw, tz=timezone.utc)
            )
            hour = start.replace(minute=0, second=0, microsecond=0)
            prices[hour] = row["mean"]

        state = self.hass.states.get(price_entity)
        if state:
            prices.update(parse_price_data(state.attributes.get("data") or []))
        return prices
