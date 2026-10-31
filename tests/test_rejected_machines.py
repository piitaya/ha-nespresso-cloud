"""A machine the cloud rejects should be parked, not polled every minute."""

import logging

import pytest
from homeassistant.helpers import entity_registry as er

from .conftest import LIVE, STALE, setup_integration
from custom_components.nespresso_cloud import const
from custom_components.nespresso_cloud.lib import Machine

MACHINE_LIST_TTL_S = const.MACHINE_LIST_TTL_S
# Only exists once rejected machines are parked; fall back so main still collects.
PARK_RETRY_MIN_S = getattr(const, "PARK_RETRY_MIN_S", 300)

unfixed = pytest.mark.xfail(
    strict=True, reason="Rejected machines are polled every minute (fixed by #6)"
)


@unfixed
async def test_rejected_machine_parked(hass, entry, fake_api, caplog):
    fake_api.machines.append(Machine(id=STALE, type="machinesB2CProfile"))
    fake_api.rejected = {STALE: 420}
    caplog.set_level(logging.WARNING)
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    for _ in range(4):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    # The live machine is polled every time, the rejected one only once.
    assert fake_api.calls[LIVE] == 5
    assert fake_api.calls[STALE] == 1
    warnings = [
        r for r in caplog.records if "nespresso" in r.name and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]

    # Its entities read unknown, not unavailable, and the live machine still works.
    reg = er.async_get(hass)
    stale_state = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{STALE}_machine_state")
    live_state = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{LIVE}_machine_state")
    assert hass.states.get(stale_state).state == "unknown"
    assert hass.states.get(live_state).state not in ("unknown", "unavailable")


@unfixed
async def test_404_also_parks(hass, entry, fake_api):
    fake_api.machines.append(Machine(id=STALE, type="machinesVenusProfile"))
    fake_api.rejected = {STALE: 404}
    await setup_integration(hass, entry)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    assert fake_api.calls[STALE] == 1


async def test_other_errors_are_not_parked(hass, entry, fake_api):
    fake_api.machines.append(Machine(id=STALE, type="machinesVenusProfile"))
    fake_api.rejected = {STALE: 500}
    await setup_integration(hass, entry)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    assert fake_api.calls[STALE] == 4


async def test_machine_that_answered_is_not_parked(hass, entry, fake_api):
    # A live machine can 404 on presence for a while during a cloud incident.
    await setup_integration(hass, entry)
    fake_api.rejected = {LIVE: 404}
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    assert fake_api.calls[LIVE] == 4


@unfixed
async def test_parked_retry_interval_grows(hass, entry, fake_api):
    fake_api.machines.append(Machine(id=STALE, type="machinesB2CProfile"))
    fake_api.rejected = {STALE: 420}
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator._parked[STALE].interval == PARK_RETRY_MIN_S

    for expected in (2 * PARK_RETRY_MIN_S, 4 * PARK_RETRY_MIN_S):
        coordinator._parked[STALE].retry_at = 0
        await coordinator.async_refresh()
        assert coordinator._parked[STALE].interval == expected
    assert fake_api.calls[STALE] == 3

    coordinator._parked[STALE].interval = MACHINE_LIST_TTL_S - 1
    coordinator._parked[STALE].retry_at = 0
    await coordinator.async_refresh()
    assert coordinator._parked[STALE].interval == MACHINE_LIST_TTL_S


@unfixed
async def test_parked_machine_resumes_when_it_answers(hass, entry, fake_api):
    fake_api.machines.append(Machine(id=STALE, type="machinesVenusProfile"))
    fake_api.rejected = {STALE: 404}
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data

    fake_api.rejected = {}
    coordinator._parked[STALE].retry_at = 0
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert STALE not in coordinator._parked
    reg = er.async_get(hass)
    state_id = reg.async_get_entity_id("sensor", "nespresso_cloud", f"{STALE}_machine_state")
    assert hass.states.get(state_id).state not in ("unknown", "unavailable")
