"""ENUM sensors must only ever report one of their options."""

import logging

import pytest
from homeassistant.helpers import entity_registry as er

from .conftest import LIVE, setup_integration


@pytest.mark.xfail(
    strict=True, reason="Unmapped codes are rejected as invalid enum states (#3, fixed by #11)"
)
async def test_unmapped_machine_status_is_unknown(hass, entry, fake_api, caplog):
    fake_api.machine_status = 999
    caplog.set_level(logging.WARNING)
    await setup_integration(hass, entry)

    reg = er.async_get(hass)
    entity_id = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{LIVE}_machine_state")
    state = hass.states.get(entity_id)
    assert state is not None, "state was never written"
    assert state.state == "unknown"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, errors


async def test_mapped_machine_status(hass, entry, fake_api):
    await setup_integration(hass, entry)
    reg = er.async_get(hass)
    entity_id = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{LIVE}_machine_state")
    state = hass.states.get(entity_id)
    assert state.state not in ("unknown", "unavailable")
    assert state.state in state.attributes["options"]
