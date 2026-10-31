"""Token rotation must not reload the entry; options changes must."""

import pytest
from homeassistant.config_entries import ConfigEntryState

from .conftest import setup_integration
from custom_components.nespresso_cloud.lib import Tokens


@pytest.mark.xfail(
    strict=True, reason="Token rotation reloads the entry every ~2h (fixed by #2)"
)
async def test_token_rotation_does_not_reload(hass, entry, fake_api):
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    await coordinator._async_persist_tokens(
        Tokens(access_token="a1", refresh_token="r1", expires_at=9_999_999_999)
    )
    await hass.async_block_till_done()

    assert entry.data["refresh_token"] == "r1"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is coordinator, "entry was reloaded"


async def test_options_change_reloads(hass, entry, fake_api):
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    hass.config_entries.async_update_entry(entry, options={"pause_when_offline": True})
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not coordinator, "options change did not reload"
    assert entry.runtime_data._pause_when_offline is True


async def test_polling_preference_change_reloads(hass, entry, fake_api):
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await hass.async_block_till_done()

    assert entry.runtime_data is not coordinator
    assert entry.runtime_data.update_interval is None
