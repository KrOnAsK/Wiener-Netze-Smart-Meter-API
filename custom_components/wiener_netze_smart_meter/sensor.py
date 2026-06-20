from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WNSmartMeterCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator: WNSmartMeterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DailyEnergySensor(coordinator, zaehlpunkt) for zaehlpunkt in coordinator.data
    )


class DailyEnergySensor(CoordinatorEntity[WNSmartMeterCoordinator], SensorEntity):
    # Informational only: no state_class, so HA does not generate long-term
    # statistics from it and it is not offered on the Energy dashboard. The
    # time-accurate source is the hourly external statistics in the coordinator.
    # The value is the latest available daily total; see the reading_date
    # attribute for the day it actually belongs to.
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_has_entity_name = True
    _attr_name = "Latest daily energy"

    def __init__(self, coordinator: WNSmartMeterCoordinator, zaehlpunkt: str) -> None:
        super().__init__(coordinator)
        self._zaehlpunkt = zaehlpunkt
        self._attr_unique_id = f"{zaehlpunkt}_daily_energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zaehlpunkt)},
            name=f"Smart meter {zaehlpunkt[-6:]}",
            manufacturer="Wiener Netze",
        )

    @property
    def native_value(self) -> float | None:
        reading = self.coordinator.data.get(self._zaehlpunkt)
        return reading.daily_wh if reading else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        reading = self.coordinator.data.get(self._zaehlpunkt)
        return {"reading_date": reading.reading_date} if reading else {}
