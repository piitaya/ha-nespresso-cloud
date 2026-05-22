"""Polling coordinator for the Nespresso Cloud integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_COUNTRY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_MACHINE_STATUS_CODES,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_DEVICE_ID,
    CONF_OWNER_ID,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SCAN_INTERVAL_ACTIVE_S,
    SCAN_INTERVAL_IDLE_S,
    SCAN_INTERVAL_OFFLINE_S,
)
from .lib import (
    Machine,
    MachineInfo,
    MachinePresence,
    MachineStatus,
    NespressoApiClient,
    NespressoAuthError,
    NespressoError,
    Tokens,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class MachineSnapshot:
    """Per-poll bundle for one machine."""

    machine: Machine
    status: MachineStatus | None
    presence: MachinePresence | None
    info: MachineInfo | None


@dataclass
class CoordinatorData:
    """Per-refresh payload exposed to entities."""

    machines: dict[str, MachineSnapshot]
    """Keyed by ``machine.id`` (the UUID, not the serial)."""


class NespressoCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Polls /status + /presence + /machineInfo for each paired machine."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator from a config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            # First refresh will retune based on actual machine state.
            update_interval=timedelta(seconds=SCAN_INTERVAL_OFFLINE_S),
            config_entry=entry,
        )
        self._entry = entry
        session = async_get_clientsession(hass)

        tokens = Tokens(
            access_token=entry.data.get(CONF_ACCESS_TOKEN, ""),
            refresh_token=entry.data[CONF_REFRESH_TOKEN],
            expires_at=float(entry.data.get(CONF_ACCESS_TOKEN_EXPIRES_AT, 0)),
        )
        self.owner_id: str | None = entry.data.get(CONF_OWNER_ID)
        self.api = NespressoApiClient(
            session=session,
            tokens=tokens,
            country=entry.data[CONF_COUNTRY],
            device_id=entry.data[CONF_DEVICE_ID],
            token_updated=self._async_persist_tokens,
        )

    async def _async_persist_tokens(self, tokens: Tokens) -> None:
        """Persist rotated tokens to the config entry.

        Critical: without this the next HA restart tries the already-
        rotated refresh_token and gets locked out.
        """
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_ACCESS_TOKEN_EXPIRES_AT: tokens.expires_at,
            },
        )

    async def _async_update_data(self) -> CoordinatorData:
        """Poll every paired machine and bundle the snapshots."""
        try:
            if not self.owner_id:
                info = await self.api.async_get_personal_info()
                self.owner_id = info.member_number
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, CONF_OWNER_ID: self.owner_id},
                )

            machines = await self.api.async_list_machines(self.owner_id)
            snapshots: dict[str, MachineSnapshot] = {}

            # A transient failure on one machine shouldn't blank the others.
            for machine in machines:
                status, presence, info = await asyncio.gather(
                    self._safe(self.api.async_get_status(self.owner_id, machine.id)),
                    self._safe(self.api.async_get_presence(self.owner_id, machine.id)),
                    self._safe(
                        self.api.async_get_machine_info(self.owner_id, machine.id)
                    ),
                )
                snapshots[machine.id] = MachineSnapshot(
                    machine=machine,
                    status=status,
                    presence=presence,
                    info=info,
                )

            self._tune_interval(snapshots)
            return CoordinatorData(machines=snapshots)

        except NespressoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NespressoError as err:
            raise UpdateFailed(str(err)) from err

    @staticmethod
    async def _safe(awaitable):
        """Await ``awaitable``, returning None on per-machine errors."""
        try:
            return await awaitable
        except NespressoAuthError:
            raise
        except NespressoError as err:
            _LOGGER.warning("Per-machine call failed: %s", err)
            return None

    def _tune_interval(self, snapshots: dict[str, MachineSnapshot]) -> None:
        """Adapt update_interval to the busiest machine in the account."""
        if not snapshots:
            target = SCAN_INTERVAL_OFFLINE_S
        else:
            picks: list[int] = []
            for snap in snapshots.values():
                connected = snap.presence is not None and snap.presence.connected
                if not connected:
                    picks.append(SCAN_INTERVAL_OFFLINE_S)
                    continue
                code = snap.status.reported.machine_status if snap.status else None
                if code is not None and code in ACTIVE_MACHINE_STATUS_CODES:
                    picks.append(SCAN_INTERVAL_ACTIVE_S)
                else:
                    picks.append(SCAN_INTERVAL_IDLE_S)
            target = min(picks)

        if self.update_interval != timedelta(seconds=target):
            _LOGGER.debug("Adapting poll interval to %ss", target)
            self.update_interval = timedelta(seconds=target)
