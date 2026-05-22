"""Config flow for the Nespresso Cloud integration."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_COUNTRY
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_DEVICE_ID,
    CONF_OWNER_ID,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from .lib import (
    NespressoApiClient,
    NespressoAuthError,
    NespressoError,
    Tokens,
    async_exchange_authorization_code,
)

_LOGGER = logging.getLogger(__name__)

CONF_AUTHORIZATION_CODE = "authorization_code"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _snippet(challenge: str) -> str:
    """Return the JS snippet the user runs in their browser console.

    Uses ``prompt()`` rather than ``navigator.clipboard`` because the
    DevTools console doesn't hold document focus, so the Clipboard API
    throws ``NotAllowedError``.
    """
    return (
        "fetch('/ecapi/identityprovider/v1/web-accounts/me/authorize"
        f"?response_type=code&code_challenge={challenge}"
        "&client_id=native-apps', "
        "{method:'POST',credentials:'include',"
        "headers:{'Content-Type':'application/json'}})"
        ".then(r=>r.json()).then(d=>prompt('Copy this code into Home Assistant:', d.authorization_code));"
    )


def _country_from_access_token(access_token: str) -> str:
    """Extract the ``market`` claim from a freshly minted access_token.

    No signature check: we just minted the token, and every API call
    re-validates auth server-side anyway.
    """
    try:
        _header, body, _sig = access_token.split(".")
        # Re-pad for stdlib base64.
        padded = body + "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        market = claims.get("market")
        if isinstance(market, str) and market:
            return market.lower()
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        _LOGGER.debug("Could not parse market claim from access_token")
    return "fr"


class NespressoCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Nespresso Cloud config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_id: str | None = None
        self._verifier: str | None = None
        self._challenge: str | None = None

    def _ensure_pkce(self) -> None:
        """Lazy-initialize the PKCE pair and device id for this flow.

        Reused across retries so a wrong-code paste doesn't invalidate
        the pending exchange.
        """
        if self._verifier is None:
            self._verifier, self._challenge = _make_pkce()
        if self._device_id is None:
            self._device_id = str(uuid.uuid4())

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the bootstrap snippet and collect the authorization_code."""
        self._ensure_pkce()
        assert self._verifier is not None
        assert self._challenge is not None
        assert self._device_id is not None

        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input[CONF_AUTHORIZATION_CODE].strip()
            try:
                tokens, owner_id, country = await self._async_finalize(code)
            except NespressoAuthError as err:
                _LOGGER.warning("Nespresso code exchange failed: %s", err)
                errors["base"] = "invalid_auth"
            except NespressoError as err:
                _LOGGER.warning("Nespresso connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(owner_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Nespresso Cloud ({country.upper()} #{owner_id})",
                    data={
                        CONF_REFRESH_TOKEN: tokens.refresh_token,
                        CONF_ACCESS_TOKEN: tokens.access_token,
                        CONF_ACCESS_TOKEN_EXPIRES_AT: tokens.expires_at,
                        CONF_COUNTRY: country,
                        CONF_OWNER_ID: owner_id,
                        CONF_DEVICE_ID: self._device_id,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_AUTHORIZATION_CODE): str}),
            description_placeholders={
                "snippet": _snippet(self._challenge),
                "url": "https://www.nespresso.com",
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, _entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Trigger the reauth flow when the refresh_token is rejected."""
        entry = self._get_reauth_entry()
        self._device_id = entry.data[CONF_DEVICE_ID]
        self._verifier, self._challenge = _make_pkce()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the browser bootstrap and swap in the new tokens."""
        assert self._verifier is not None
        assert self._challenge is not None
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            code = user_input[CONF_AUTHORIZATION_CODE].strip()
            try:
                tokens, owner_id, _country = await self._async_finalize(code)
            except NespressoAuthError:
                errors["base"] = "invalid_auth"
            except NespressoError:
                errors["base"] = "cannot_connect"
            else:
                if owner_id != entry.data.get(CONF_OWNER_ID):
                    errors["base"] = "different_account"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_REFRESH_TOKEN: tokens.refresh_token,
                            CONF_ACCESS_TOKEN: tokens.access_token,
                            CONF_ACCESS_TOKEN_EXPIRES_AT: tokens.expires_at,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_AUTHORIZATION_CODE): str}),
            description_placeholders={
                "snippet": _snippet(self._challenge),
                "url": "https://www.nespresso.com",
            },
            errors=errors,
        )

    def _get_reauth_entry(self) -> ConfigEntry:
        """Return the config entry that triggered the reauth flow."""
        entry_id = self.context.get("entry_id")
        assert entry_id is not None
        entry = self.hass.config_entries.async_get_entry(entry_id)
        assert entry is not None
        return entry

    async def _async_finalize(self, code: str) -> tuple[Tokens, str, str]:
        """Exchange code → tokens, derive country from JWT, fetch ownerId."""
        assert self._verifier is not None
        assert self._device_id is not None

        session = async_get_clientsession(self.hass)
        tokens = await async_exchange_authorization_code(
            session,
            code=code,
            code_verifier=self._verifier,
            device_id=self._device_id,
        )
        country = _country_from_access_token(tokens.access_token)

        async def _noop(_t: Tokens) -> None:
            return None

        api = NespressoApiClient(
            session=session,
            tokens=tokens,
            country=country,
            device_id=self._device_id,
            token_updated=_noop,
        )
        info = await api.async_get_personal_info()
        return tokens, info.member_number, country
