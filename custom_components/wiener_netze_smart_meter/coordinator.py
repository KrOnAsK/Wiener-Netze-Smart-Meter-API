from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import partial

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

# HA 2026.11 drops has_mean in favour of mean_type. Use mean_type where the
# enum exists, fall back to has_mean on older cores.
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_FIELD = {"mean_type": StatisticMeanType.NONE}
except ImportError:  # HA without StatisticMeanType
    _MEAN_FIELD = {"has_mean": False}


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

    # --- statistics metadata helpers ---

    def _energy_metadata(self, zaehlpunkt: str) -> StatisticMetaData:
        return StatisticMetaData(
            **_MEAN_FIELD,
            has_sum=True,
            name=f"Smart meter {zaehlpunkt[-6:]} hourly energy",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:{zaehlpunkt.lower()}_hourly_energy",
            unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        )

    def _cost_metadata(self, zaehlpunkt: str) -> StatisticMetaData:
        return StatisticMetaData(
            **_MEAN_FIELD,
            has_sum=True,
            name=f"Smart meter {zaehlpunkt[-6:]} hourly cost",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:{zaehlpunkt.lower()}_hourly_cost",
            unit_of_measurement=COST_CURRENCY,
        )

    async def _last_sum(self, statistic_id: str) -> tuple[float, datetime | None]:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        if last.get(statistic_id):
            row = last[statistic_id][0]
            return row["sum"], datetime.fromtimestamp(row["start"], tz=timezone.utc)
        return 0.0, None

    # --- incremental imports (every update) ---

    async def _import_hourly_statistics(self, zaehlpunkt: str) -> None:
        metadata = self._energy_metadata(zaehlpunkt)
        total, start_after = await self._last_sum(metadata["statistic_id"])
        if start_after is not None:
            von = start_after.strftime("%Y-%m-%d")
        else:
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

        if statistics:
            async_add_external_statistics(self.hass, metadata, statistics)

    async def _import_cost_statistics(self, zaehlpunkt: str) -> None:
        price_entity = self.entry.options.get(CONF_PRICE_ENTITY)
        if not price_entity:
            return

        try:
            metadata = self._cost_metadata(zaehlpunkt)
            total, start_after = await self._last_sum(metadata["statistic_id"])
            window_start = start_after or (
                datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
            )
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
            _LOGGER.debug(
                "cost(update) %s: energy_hours=%d price_hours=%d rows=%d entity=%s",
                zaehlpunkt,
                len(energy_buckets),
                len(price_map),
                len(rows),
                price_entity,
            )
            if rows:
                stats = [StatisticData(start=h, state=c, sum=s) for h, c, s in rows]
                async_add_external_statistics(self.hass, metadata, stats)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Cost import failed for %s", zaehlpunkt)

    # --- full history (on-demand service) ---

    async def async_import_full_history(self) -> None:
        """Re-import the full available history (API default ~3 years) from
        scratch, overwriting existing statistics with a clean cumulative sum."""
        for zaehlpunkt in list(self.data):
            _LOGGER.info("Importing full history for %s", zaehlpunkt)
            messwerte = await self.hass.async_add_executor_job(
                partial(
                    quarter_hour_messwerte,
                    self.client,
                    zaehlpunkt,
                    None,
                    None,
                    paginate=True,
                )
            )
            buckets = bucket_hourly(messwerte)
            if not buckets:
                continue

            total = 0.0
            energy_stats: list[StatisticData] = []
            for start, wh in buckets:
                total += wh
                energy_stats.append(StatisticData(start=start, state=wh, sum=total))
            async_add_external_statistics(
                self.hass, self._energy_metadata(zaehlpunkt), energy_stats
            )

            price_entity = self.entry.options.get(CONF_PRICE_ENTITY)
            if price_entity:
                try:
                    price_map = await self._build_price_map(
                        price_entity, buckets[0][0], datetime.now(timezone.utc)
                    )
                    rows = compute_hourly_cost(buckets, price_map, starting_total=0.0)
                    _LOGGER.debug(
                        "cost(full) %s: energy_hours=%d price_hours=%d rows=%d entity=%s",
                        zaehlpunkt,
                        len(buckets),
                        len(price_map),
                        len(rows),
                        price_entity,
                    )
                    if rows:
                        stats = [
                            StatisticData(start=h, state=c, sum=s) for h, c, s in rows
                        ]
                        async_add_external_statistics(
                            self.hass, self._cost_metadata(zaehlpunkt), stats
                        )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Full-history cost import failed for %s", zaehlpunkt)
            _LOGGER.info("Full history import done for %s", zaehlpunkt)

    # --- price lookup ---

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
