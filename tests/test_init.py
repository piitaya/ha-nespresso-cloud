"""End-to-end tests driving the integration through a mocked cloud."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.nespresso_cloud.const import DOMAIN
from custom_components.nespresso_cloud.lib import (
    Machine,
    MachineInfo,
    MachinePresence,
    MachineStatus,
    NespressoError,
)

MACHINE_ID = "uuid-1"
SERIAL = "SN123456"

# (machineStatus, errorCode, descalingAlert)
READY = (2, 0, False)
CAPSULE_READING = (17, 0, False)
BREWING = (4, 0, False)
UPDATING = (13, 0, True)


class FakeApi:
    """Stands in for NespressoApiClient; scripted via class attributes."""

    statuses: list[tuple[int, int, bool]] = []
    """Served in order; the last one repeats once the script runs out."""
    connected = True

    def __init__(self, **_kwargs) -> None:
        """Accept and ignore the real client's constructor arguments."""

    async def async_get_personal_info(self):
        raise AssertionError("owner_id is in the entry, should not be fetched")

    async def async_list_machines(self, owner_id):
        return [
            Machine.from_response(
                {
                    "id": MACHINE_ID,
                    "type": "machinesVenus1Plus1DLProfile",
                    "serialNumber": SERIAL,
                    "customName": "Coffee",
                    "wiFiName": "HomeNet",
                    "wiFiMacAddress": "AA:BB:CC:DD:EE:FF",
                    "pairingKey": "secret",
                }
            )
        ]

    async def async_get_presence(self, owner_id, machine_id):
        return MachinePresence.from_response(
            {"Connected": FakeApi.connected, "MachineID": SERIAL}
        )

    async def async_get_status(self, owner_id, machine_id):
        if len(FakeApi.statuses) > 1:
            code, error, descaling = FakeApi.statuses.pop(0)
        else:
            code, error, descaling = FakeApi.statuses[0]
        return MachineStatus.from_response(
            {
                "sync": True,
                "lastReportedUpdate": 1700000000,
                "reported": {
                    "machineStatus": code,
                    "errorCode": error,
                    "descalingAlert": descaling,
                    "lastCoffeeFamilyID": 7,
                    "fotaStatus": 0,
                    "volumeCustomization": "v1",
                    "serialNumber": SERIAL,
                },
            }
        )

    async def async_get_machine_info(self, owner_id, machine_id):
        return MachineInfo.from_response(
            {
                "machinefw": "1.2",
                "connectivityfw": "3.4",
                "recipeversion": "55",
                "machineid": SERIAL,
            }
        )


async def _setup(hass: HomeAssistant, statuses) -> MockConfigEntry:
    FakeApi.statuses = list(statuses)
    FakeApi.connected = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="acct",
        unique_id="owner",
        data={
            "refresh_token": "rt",
            "access_token": "at",
            "access_token_expires_at": 9e12,
            "country": "fr",
            "owner_id": "owner",
            "device_id": "dev",
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.nespresso_cloud.coordinator.NespressoApiClient", FakeApi
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 60
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{MACHINE_ID}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


def _record_events(hass: HomeAssistant, event_id: str) -> list[State]:
    fired: list[State] = []
    hass.bus.async_listen(
        "state_changed",
        lambda e: e.data["entity_id"] == event_id
        and fired.append(e.data["new_state"]),
    )
    return fired


async def test_brew_cycle_events_and_counter(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A brew fires start/finish once and bumps the counter once."""
    await _setup(hass, [READY])
    brews_id = _entity_id(hass, "sensor", "brews")
    assert hass.states.get(brews_id).state == "0"
    fired = _record_events(hass, _entity_id(hass, "event", "machine_event"))

    FakeApi.statuses = [CAPSULE_READING, BREWING, BREWING, READY]
    for _ in range(4):
        await _tick(hass, freezer)

    assert [s.attributes["event_type"] for s in fired] == [
        "brew_started",
        "brew_finished",
    ]
    finished = fired[1].attributes
    assert finished["coffee_family_id"] == 7
    assert finished["duration"] >= 60
    assert hass.states.get(brews_id).state == "1"


async def test_same_poll_events_all_fire(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Several transitions in one poll each reach the event entity."""
    await _setup(hass, [READY])
    fired = _record_events(hass, _entity_id(hass, "event", "machine_event"))

    FakeApi.statuses = [(2, 203, False), (19, 202, True)]
    for _ in range(2):
        await _tick(hass, freezer)

    assert [s.attributes["event_type"] for s in fired] == [
        "error",
        "water_tank_empty",
        "error",
        "descaling_required",
    ]
    assert fired[0].attributes["error"] == "lever_open"
    assert fired[0].attributes["code"] == 203


async def test_failed_refresh_does_not_double_count(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A failed refresh re-notifies with old data; events must not replay."""
    entry = await _setup(hass, [READY])
    brews_id = _entity_id(hass, "sensor", "brews")
    FakeApi.statuses = [BREWING]
    await _tick(hass, freezer)
    assert hass.states.get(brews_id).state == "1"

    async def _boom():
        raise NespressoError("cloud hiccup")

    coordinator = entry.runtime_data
    with patch.object(coordinator, "_async_machines", _boom):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(brews_id).state == "1"


async def test_offline_and_reconnect(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Offline keeps cached data; reconnecting doesn't replay stale edges."""
    await _setup(hass, [READY])
    event_id = _entity_id(hass, "event", "machine_event")
    brews_id = _entity_id(hass, "sensor", "brews")
    FakeApi.statuses = [BREWING]
    await _tick(hass, freezer)

    FakeApi.connected = False
    await _tick(hass, freezer, 120)
    assert hass.states.get(brews_id).state == "1"
    assert hass.states.get(event_id).state != "unavailable"
    for key, value in (
        ("connectivity_firmware", "3.4"),
        ("recipe_version", "55"),
        ("wifi_network", "HomeNet"),
    ):
        assert hass.states.get(_entity_id(hass, "sensor", key)).state == value

    # Back online in a different state: the brew we never saw end must not
    # produce a brew_finished.
    fired = _record_events(hass, event_id)
    FakeApi.statuses = [READY]
    FakeApi.connected = True
    await _tick(hass, freezer, 120)
    assert fired == []


async def test_device_and_disabled_entities(hass: HomeAssistant) -> None:
    """The Wi-Fi MAC links the device; raw fields start disabled."""
    entry = await _setup(hass, [READY])
    (device,) = dr.async_entries_for_config_entry(
        dr.async_get(hass), entry.entry_id
    )
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections

    registry = er.async_get(hass)
    for key in (
        "last_coffee_family",
        "fota_status",
        "volume_customization",
        "temperature_customization",
        "recipe_tag",
    ):
        assert registry.async_get(_entity_id(hass, "sensor", key)).disabled_by


async def test_firmware_updating_and_no_first_poll_events(
    hass: HomeAssistant,
) -> None:
    """Status 13 drives the binary sensor; the first poll fires nothing."""
    await _setup(hass, [UPDATING])
    updating_id = _entity_id(hass, "binary_sensor", "firmware_updating")
    assert hass.states.get(updating_id).state == "on"
    event_id = _entity_id(hass, "event", "machine_event")
    assert hass.states.get(event_id).state == "unknown"


async def test_counter_restores(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The brew total survives a restart and keeps counting."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("sensor.coffee_brews", "41"),
                {"native_value": 41, "native_unit_of_measurement": None},
            )
        ],
    )
    await _setup(hass, [READY, BREWING])
    brews_id = _entity_id(hass, "sensor", "brews")
    assert brews_id == "sensor.coffee_brews"
    assert hass.states.get(brews_id).state == "41"
    await _tick(hass, freezer)
    assert hass.states.get(brews_id).state == "42"
    assert hass.states.get(brews_id).attributes["unit_of_measurement"] == "brews"


async def test_diagnostics_redacted(hass: HomeAssistant, hass_client) -> None:
    """Diagnostics carry the raw payload with every identifier redacted."""
    entry = await _setup(hass, [READY])
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    text = str(diag)
    for secret in (SERIAL, "secret", "HomeNet", "AA:BB", "'rt'", "'at'", MACHINE_ID):
        assert secret not in text, secret
    (machine,) = diag["machines"]
    assert machine["status"]["raw"]["reported"]["fotaStatus"] == 0
    assert machine["info"]["connectivity_fw"] == "3.4"
