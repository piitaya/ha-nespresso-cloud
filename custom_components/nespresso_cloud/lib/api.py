"""Async Nespresso ECapi client.

Refresh tokens rotate on every refresh, so the caller MUST plug a
``token_updated`` callback that persists the rotated value — otherwise
the next restart locks the account out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .devices import descriptor_for
from .models import (
    Machine,
    MachineInfo,
    MachinePresence,
    MachineStatus,
    PersonalInfo,
    Tokens,
)

_LOGGER = logging.getLogger(__name__)

ECAPI_BASE = "https://www.nespresso.com"
OAUTH_CLIENT_ID = "native-apps"
APP_NAME_VERSION = "nn-smart-app-b2c-android/1.2.8"

def _user_agent_for(device_id: str) -> str:
    """Pick a stable User-Agent based on the install's device_id."""
    android, vendor, model = descriptor_for(device_id)
    return f"{APP_NAME_VERSION} ({android}; {vendor}) {model}"

# Decoded JWT exp - iat = 7200s. Refresh a bit early to avoid 401 races.
ACCESS_TOKEN_REFRESH_LEEWAY_S = 300

# Integer enums for the cloud-reported machine fields. Unknown values
# are surfaced as ``"unknown_<n>"`` so we never silently drop data.
MACHINE_STATUS_LABELS: dict[int, str] = {
    0: "reset",
    1: "heat_up",
    2: "ready",
    3: "descaling_ready",
    4: "brewing",
    5: "cleaning",
    6: "descaling",
    7: "emptying",
    8: "device_error",
    9: "low_power",
    10: "cool_down",
    11: "service_mode",
    12: "standby",
    13: "updating",
    14: "rinsing",
    17: "capsule_reading",
    18: "descale_sequence_decoding",
    19: "tank_empty",
    20: "descaling_paused",
    21: "initialization",
    22: "rinsing_ready",
    23: "ready_blocked",
    26: "cleaning_paused",
    33: "emptying_ready",
    34: "cleaning_ready",
    35: "old_capsule_detected",
    36: "rinsing_paused",
    100: "hard_reset_running",
    101: "soft_reset_running",
}

MILK_UNIT_STATUS_LABELS: dict[int, str] = {
    0: "offline",
    1: "ready",
    2: "frothing",
    4: "cleaning",
    5: "heat_up",
    6: "descaling_ready",
    7: "descaling_active",
    8: "rinsing_ready",
    9: "rinsing_active",
    10: "blocked_menu",
    11: "blocked_cleaning_needed",
    12: "blocked_descaling_needed",
    13: "waiting",
    14: "descaling_paused",
    15: "rinsing_paused",
}

ERROR_ORIGIN_LABELS: dict[int, str] = {
    0: "none",
    1: "power_line",
    2: "mmi",
    3: "main_system",
    4: "sensor",
    5: "actuator",
    6: "other",
    255: "unknown",
}

# Labels collapsed to the same concise categories the Nespresso app uses
# in its error banners — multiple raw codes share one user-facing label.
ERROR_CODE_LABELS: dict[int, str] = {
    0: "none",
    11: "descaling_required",
    200: "capsule_detected",
    201: "emptying_aborted",
    202: "water_tank_empty",
    203: "lever_open",
    204: "old_capsule",
    206: "no_capsule_detected",
    207: "drying_interrupted",
    210: "capsule_not_supported",
    211: "emptying_aborted",
    212: "process_stopped",
    218: "lever_open",
    219: "lever_open",
    220: "lever_open",
    221: "lever_open",
    222: "descaling_interrupted",
    223: "top_up_not_allowed",
    224: "capsule_detected",
    10009: "cooldown_started",
    10113: "machine_failure",
    10123: "machine_failure",
    10130: "capsule_reading_failed",
    10132: "no_water_flow",
    10300: "capsule_detected",
    10301: "lever_open",
    10302: "lever_open",
    10303: "process_stopped",
}


class NespressoError(Exception):
    """Base class for ECapi errors."""


class NespressoAuthError(NespressoError):
    """Raised when the refresh_token is invalid or revoked.

    HA should surface this as a config entry auth failure so the user
    can re-bootstrap with a fresh refresh_token.
    """


TokenUpdatedCallback = Callable[[Tokens], Awaitable[None]]


async def async_exchange_authorization_code(
    session: aiohttp.ClientSession,
    *,
    code: str,
    code_verifier: str,
    device_id: str,
) -> Tokens:
    """Exchange a PKCE authorization_code for a tokens pair."""
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": OAUTH_CLIENT_ID,
    }
    url = f"{ECAPI_BASE}/ecapi/identityprovider/v1/authorize"
    user_agent = _user_agent_for(device_id)
    headers = {
        "User-Agent": user_agent,
        "x-client": user_agent,
        "x-device": device_id,
        "Accept": "application/json",
        "Content-Type": "application/json;charset=utf-8",
    }
    try:
        async with session.post(
            url, params=params, headers=headers, data=b""
        ) as resp:
            body_text = await resp.text()
            if resp.status >= 400:
                raise NespressoAuthError(
                    f"Code exchange failed ({resp.status}): {body_text[:200]}"
                )
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        raise NespressoError(f"Code exchange network error: {err}") from err
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=time.time() + int(data.get("expires_in", 7200)),
    )


class NespressoApiClient:
    """Serializes refresh requests behind a lock so concurrent 401s don't race."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens,
        country: str,
        device_id: str,
        token_updated: TokenUpdatedCallback,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._country = country.lower()
        self._device_id = device_id
        self._user_agent = _user_agent_for(device_id)
        self._token_updated = token_updated
        self._refresh_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def async_ensure_token(self) -> str:
        """Return a usable access_token, refreshing if it's near expiry."""
        if self._tokens.expires_at - ACCESS_TOKEN_REFRESH_LEEWAY_S > time.time():
            return self._tokens.access_token
        return await self._async_refresh()

    async def _async_refresh(self) -> str:
        """Rotate the refresh_token and persist via ``token_updated``."""
        async with self._refresh_lock:
            # Re-check inside the lock — another caller may have refreshed
            # while we waited.
            if self._tokens.expires_at - ACCESS_TOKEN_REFRESH_LEEWAY_S > time.time():
                return self._tokens.access_token

            _LOGGER.debug("Refreshing Nespresso access token")
            params = {
                "refresh_token": self._tokens.refresh_token,
                "grant_type": "refresh_token",
                "client_id": OAUTH_CLIENT_ID,
            }
            url = f"{ECAPI_BASE}/ecapi/identityprovider/v1/authorize"
            headers = {
                "User-Agent": self._user_agent,
                "x-client": self._user_agent,
                "x-device": self._device_id,
                "Accept": "application/json",
                "Content-Type": "application/json;charset=utf-8",
            }
            try:
                async with self._session.post(
                    url, params=params, headers=headers, data=b""
                ) as resp:
                    body_text = await resp.text()
                    if resp.status == 400 or resp.status == 401:
                        raise NespressoAuthError(
                            f"Refresh failed ({resp.status}): {body_text[:200]}"
                        )
                    if resp.status >= 400:
                        raise NespressoError(
                            f"Refresh failed ({resp.status}): {body_text[:200]}"
                        )
                    data = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise NespressoError(f"Refresh network error: {err}") from err

            self._tokens = Tokens(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=time.time() + int(data.get("expires_in", 7200)),
            )
            # Persist rotated token. Failure to persist = locked out on
            # the next HA restart, so we bubble up.
            await self._token_updated(self._tokens)
            return self._tokens.access_token

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> Any:
        """Make an authenticated request with a single 401-retry."""
        access_token = await self.async_ensure_token()
        url = f"{ECAPI_BASE}{path}"
        headers = {
            "User-Agent": self._user_agent,
            "x-client": self._user_agent,
            "x-device": self._device_id,
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with self._session.request(
                method, url, headers=headers, json=json_body
            ) as resp:
                if resp.status == 401:
                    # Token wedged mid-flight. One retry with a forced refresh.
                    _LOGGER.debug("Got 401 on %s — forcing token refresh", path)
                    self._tokens.expires_at = 0
                    access_token = await self.async_ensure_token()
                    headers["Authorization"] = f"Bearer {access_token}"
                    async with self._session.request(
                        method, url, headers=headers, json=json_body
                    ) as resp2:
                        return await _read_json_or_raise(resp2, path)
                return await _read_json_or_raise(resp, path)
        except aiohttp.ClientError as err:
            raise NespressoError(f"Network error on {path}: {err}") from err

    # ------------------------------------------------------------------
    # ECapi endpoints
    # ------------------------------------------------------------------

    async def async_get_personal_info(self) -> PersonalInfo:
        """Fetch the account's personal info (carries the ownerId)."""
        data = await self._request(
            "GET",
            f"/ecapi/customers/v7/{self._country}/b2c/me/personal-info",
        )
        return PersonalInfo.from_response(data)

    async def async_list_machines(self, owner_id: str) -> list[Machine]:
        """List the machines paired to the account."""
        data = await self._request(
            "GET",
            f"/ecapi/machines/v1/{self._country}/b2c/{owner_id}",
        )
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("machines") or data.get("items") or []
        else:
            items = []
        return [Machine.from_response(m) for m in items]

    async def async_get_status(self, owner_id: str, machine_id: str) -> MachineStatus:
        """Fetch the machine's reported status payload."""
        data = await self._request(
            "GET",
            f"/ecapi/machines/v1/{self._country}/b2c/{owner_id}/{machine_id}/status",
        )
        return MachineStatus.from_response(data)

    async def async_get_presence(
        self, owner_id: str, machine_id: str
    ) -> MachinePresence:
        """Fetch the machine's cloud connectivity status."""
        data = await self._request(
            "GET",
            f"/ecapi/machines/v1/{self._country}/b2c/{owner_id}/{machine_id}/presence",
        )
        return MachinePresence.from_response(data)

    async def async_get_machine_info(
        self, owner_id: str, machine_id: str
    ) -> MachineInfo:
        """Fetch the machine's firmware/brand info."""
        data = await self._request(
            "GET",
            f"/ecapi/machines/v1/{self._country}/b2c/{owner_id}/{machine_id}/machineInfo",
        )
        return MachineInfo.from_response(data)

async def _read_json_or_raise(resp: aiohttp.ClientResponse, path: str) -> Any:
    """Decode the JSON body, mapping HTTP errors to Nespresso* exceptions."""
    if resp.status == 401:
        body = await resp.text()
        raise NespressoAuthError(f"401 on {path}: {body[:200]}")
    if resp.status >= 400:
        body = await resp.text()
        raise NespressoError(f"{resp.status} on {path}: {body[:200]}")
    return await resp.json(content_type=None)


def _label(table: dict[int, str], value: int | None) -> str | None:
    if value is None:
        return None
    return table.get(value, f"unknown_{value}")


def machine_status_label(value: int | None) -> str | None:
    """Return the stable label for a ``reported.machineStatus`` value."""
    return _label(MACHINE_STATUS_LABELS, value)


def milk_unit_status_label(value: int | None) -> str | None:
    """Return the stable label for a ``reported.milkUnitStatus`` value."""
    return _label(MILK_UNIT_STATUS_LABELS, value)


def error_origin_label(value: int | None) -> str | None:
    """Return the stable label for a ``reported.errorOrigin`` value."""
    return _label(ERROR_ORIGIN_LABELS, value)


def error_code_label(value: int | None) -> str | None:
    """Return the stable label for a ``reported.errorCode`` value."""
    return _label(ERROR_CODE_LABELS, value)
