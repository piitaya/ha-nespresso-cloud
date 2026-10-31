"""Diagnostics support for the Nespresso Cloud integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NespressoCloudEntry
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_OWNER_ID,
    CONF_REFRESH_TOKEN,
)

TO_REDACT = {
    # Config entry
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_ID,
    CONF_OWNER_ID,
    # Machine list
    "serial_number",
    "pairing_key",
    "mac_address",
    "wifi_mac_address",
    "wifi_name",
    "id",
    "machine_id",
    # Presence / machineInfo
    "principal_id",
    "machineid",
    # Raw status payload
    "serialNumber",
    "pairingKey",
    "macAddress",
    "wiFiMacAddress",
    "wiFiName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NespressoCloudEntry
) -> dict[str, Any]:
    """Return the entry config and the latest per-machine snapshot."""
    coordinator = entry.runtime_data
    data = coordinator.data
    machines = (
        [
            {
                "machine": asdict(snap.machine),
                "status": asdict(snap.status) if snap.status else None,
                "presence": asdict(snap.presence) if snap.presence else None,
                "info": asdict(snap.info) if snap.info else None,
            }
            for snap in data.machines.values()
        ]
        if data
        else []
    )
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "machines": machines,
        },
        TO_REDACT,
    )
