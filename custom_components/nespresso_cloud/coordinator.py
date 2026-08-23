"""Polling coordinator for the Nespresso Cloud integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_COUNTRY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_HOLD_S,
    ACTIVE_MACHINE_STATUS_CODES,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_DEVICE_ID,
    CONF_OWNER_ID,
    CONF_PAUSE_WHEN_OFFLINE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    ERROR_BACKOFF_BASE_S,
    ERROR_BACKOFF_MAX_S,
    MACHINE_INFO_TTL_S,
    MACHINE_LIST_TTL_S,
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
        # Honour the "Enable polling" system option: when off, only manual
        # refreshes (update_entity, automations) drive updates.
        self._polling_disabled = bool(entry.pref_disable_polling)
        self._pause_when_offline = bool(
            entry.options.get(CONF_PAUSE_WHEN_OFFLINE, False)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            # First refresh will retune based on actual machine state.
            update_interval=(
                None
                if self._polling_disabled
                else timedelta(seconds=SCAN_INTERVAL_OFFLINE_S)
            ),
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
        self._machines_cache: list[Machine] | None = None
        self._machines_fetched_at = 0.0
        self._info_cache: dict[str, MachineInfo] = {}
        self._info_fetched_at: dict[str, float] = {}
        self._error_backoff = 0
        self._connected_ids: set[str] = set()
        self._active_until = 0.0
        self._last_status: dict[str, MachineStatus] = {}

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

            machines = await self._async_machines()
            snapshots: dict[str, MachineSnapshot] = {}

            for machine in machines:
                presence = await self._safe(
                    self.api.async_get_presence(self.owner_id, machine.id)
                )
                connected = presence is not None and presence.connected

                if connected:
                    status = await self._safe(
                        self.api.async_get_status(self.owner_id, machine.id)
                    )
                    if status is not None:
                        self._last_status[machine.id] = status
                    else:
                        status = self._last_status.get(machine.id)
                else:
                    status = self._last_status.get(machine.id)

                snapshots[machine.id] = MachineSnapshot(
                    machine=machine,
                    status=status,
                    presence=presence,
                    info=await self._async_machine_info(machine.id),
                )

            connected_now = {
                mid
                for mid, snap in snapshots.items()
                if snap.presence is not None and snap.presence.connected
            }
            # Coming online means someone woke the machine to use it, so poll
            # fast for a bit even before a brewing status shows up.
            if connected_now - self._connected_ids:
                self._active_until = time.time() + ACTIVE_HOLD_S
            self._connected_ids = connected_now

            self._tune_interval(snapshots)
            self._error_backoff = 0
            return CoordinatorData(machines=snapshots)

        except NespressoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NespressoError as err:
            self._apply_backoff()
            raise UpdateFailed(str(err)) from err

    async def _async_machines(self) -> list[Machine]:
        """Return the paired machines, refetching at most once per TTL."""
        fresh = (
            self._machines_cache is None
            or time.time() - self._machines_fetched_at >= MACHINE_LIST_TTL_S
        )
        if not fresh:
            return self._machines_cache

        try:
            machines = await self.api.async_list_machines(self.owner_id)
        except NespressoAuthError:
            raise
        except NespressoError as err:
            # Serve the stale list rather than blank every entity on a hiccup.
            if self._machines_cache is not None:
                _LOGGER.warning("Machine list refresh failed, using cache: %s", err)
                return self._machines_cache
            raise

        self._machines_cache = machines
        self._machines_fetched_at = time.time()
        return machines

    async def _async_machine_info(self, machine_id: str) -> MachineInfo | None:
        """Return firmware/brand info, refetching at most once per TTL."""
        cached = self._info_cache.get(machine_id)
        if (
            cached is not None
            and time.time() - self._info_fetched_at.get(machine_id, 0.0)
            < MACHINE_INFO_TTL_S
        ):
            return cached

        fresh = await self._safe(
            self.api.async_get_machine_info(self.owner_id, machine_id)
        )
        if fresh is None:
            return cached
        self._info_cache[machine_id] = fresh
        self._info_fetched_at[machine_id] = time.time()
        return fresh

    def _apply_backoff(self) -> None:
        """Slow the poll after repeated cloud errors, capped."""
        if self._polling_disabled:
            return
        # Bound the counter so the exponent can't run away; min() caps the delay.
        self._error_backoff = min(self._error_backoff + 1, 10)
        target = min(
            ERROR_BACKOFF_BASE_S * 2 ** (self._error_backoff - 1),
            ERROR_BACKOFF_MAX_S,
        )
        if self.update_interval != timedelta(seconds=target):
            _LOGGER.debug("Backing poll off to %ss after errors", target)
            self.update_interval = timedelta(seconds=target)

    async def _safe(self, awaitable):
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
        if self._polling_disabled:
            return

        connected_any = any(
            snap.presence is not None and snap.presence.connected
            for snap in snapshots.values()
        )
        in_burst = time.time() < self._active_until

        if self._pause_when_offline and not connected_any and not in_burst:
            # Nothing to watch until an external trigger (e.g. a smart-plug
            # automation) refreshes us; the idle poll already caught the
            # disconnect on its way here.
            new_interval: timedelta | None = None
        else:
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
            if in_burst:
                target = min(target, SCAN_INTERVAL_ACTIVE_S)
            new_interval = timedelta(seconds=target)

        if self.update_interval != new_interval:
            _LOGGER.debug("Adapting poll interval to %s", new_interval)
            self.update_interval = new_interval
