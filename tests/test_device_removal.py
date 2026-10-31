"""Devices can be removed only once the account no longer lists them."""

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from .conftest import LIVE, STALE, setup_integration
from custom_components.nespresso_cloud.const import DOMAIN

unfixed = pytest.mark.xfail(
    strict=True, reason="No async_remove_config_entry_device hook (fixed by #12)"
)


async def _remove(hass, hass_ws_client, entry, device_id):
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device_id,
        }
    )
    return await client.receive_json()


@unfixed
async def test_stale_device_can_be_removed(hass, hass_ws_client, entry, fake_api):
    await setup_integration(hass, entry)
    reg = dr.async_get(hass)
    stale = reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, STALE)}, name="Old machine"
    )

    response = await _remove(hass, hass_ws_client, entry, stale.id)

    assert response["success"], response
    assert reg.async_get(stale.id) is None


async def test_listed_device_cannot_be_removed(hass, hass_ws_client, entry, fake_api):
    await setup_integration(hass, entry)
    reg = dr.async_get(hass)
    live = reg.async_get_device_by_identifier((DOMAIN, LIVE), entry.entry_id)
    assert live is not None

    response = await _remove(hass, hass_ws_client, entry, live.id)

    assert not response["success"]
    assert reg.async_get(live.id) is not None


@unfixed
async def test_removal_allowed_while_not_loaded(hass, hass_ws_client, entry, fake_api):
    await setup_integration(hass, entry)
    reg = dr.async_get(hass)
    stale = reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, STALE)}, name="Old machine"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)

    response = await _remove(hass, hass_ws_client, entry, stale.id)

    assert response["success"], response
    assert reg.async_get(stale.id) is None


@unfixed
async def test_listed_device_removed_while_not_loaded_comes_back(
    hass, hass_ws_client, entry, fake_api
):
    await setup_integration(hass, entry)
    reg = dr.async_get(hass)
    live = reg.async_get_device_by_identifier((DOMAIN, LIVE), entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)

    response = await _remove(hass, hass_ws_client, entry, live.id)
    assert response["success"], response
    assert reg.async_get_device_by_identifier((DOMAIN, LIVE), entry.entry_id) is None

    # The account still lists the machine, so setup brings its device back.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_device_by_identifier((DOMAIN, LIVE), entry.entry_id) is not None
