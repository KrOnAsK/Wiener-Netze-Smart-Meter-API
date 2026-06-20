from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from wiener_netze_smart_meter_api import WNAPIClient
from wiener_netze_smart_meter_api.exceptions import WNAPIAuthenticationError

from .const import BACKFILL_DAYS, DOMAIN, UPDATE_INTERVAL_HOURS
from .logic import (
    MeterReading,
    bucket_hourly,
    latest_daily_reading,
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
