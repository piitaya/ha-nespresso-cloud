"""A failing machineInfo should be retried once per TTL, not every poll."""

import logging

import pytest
from homeassistant.helpers import entity_registry as er

from .conftest import LIVE, setup_integration
from custom_components.nespresso_cloud.const import MACHINE_INFO_TTL_S


@pytest.mark.xfail(
    strict=True, reason="A failing machineInfo is refetched every poll (fixed by #7)"
)
async def test_failing_info_retried_once_per_ttl(hass, entry, fake_api, caplog):
    fake_api.info_failing = {LIVE}
    caplog.set_level(logging.WARNING)
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    for _ in range(4):
        await coordinator.async_refresh()
    assert fake_api.calls[LIVE] == 5, "presence still polled every time"
    assert fake_api.calls[f"info:{LIVE}"] == 1
    info_warnings = [r for r in caplog.records if "machineInfo" in r.getMessage()]
    assert len(info_warnings) == 1

    # The machine itself keeps working while its info is missing.
    reg = er.async_get(hass)
    state_id = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{LIVE}_machine_state")
    assert hass.states.get(state_id).state not in ("unknown", "unavailable")

    # Once the TTL has passed it is retried exactly once more.
    coordinator._info_fetched_at[LIVE] -= MACHINE_INFO_TTL_S + 1
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert fake_api.calls[f"info:{LIVE}"] == 2


async def test_successful_info_cached(hass, entry, fake_api):
    await setup_integration(hass, entry)
    for _ in range(4):
        await entry.runtime_data.async_refresh()
    assert fake_api.calls[f"info:{LIVE}"] == 1
