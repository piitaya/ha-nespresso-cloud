"""Shared fixtures for the Nespresso Cloud tests."""

from __future__ import annotations

import socket
import sys
from unittest.mock import patch

import pytest
import pytest_socket
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nespresso_cloud.const import DOMAIN
from custom_components.nespresso_cloud.lib import (
    Machine,
    MachineInfo,
    MachinePresence,
    MachineStatus,
    NespressoError,
)
from custom_components.nespresso_cloud.lib.models import ReportedStatus

LIVE = "11111111-aaaa-bbbb-cccc-000000000001"
STALE = "22222222-aaaa-bbbb-cccc-000000000002"

if sys.platform == "win32":
    # HA's test plugin blocks every non-Unix socket, but asyncio on Windows
    # wakes its event loop through a loopback socketpair. Exempt only that
    # call; any other socket a test opens is still blocked.
    _blocked_socketpair = socket.socketpair

    def _loop_socketpair(*args, **kwargs):
        guarded = socket.socket
        socket.socket = pytest_socket._true_socket
        try:
            return _blocked_socketpair(*args, **kwargs)
        finally:
            socket.socket = guarded

    socket.socketpair = _loop_socketpair


def _error(message: str, status: int) -> NespressoError:
    # NespressoError may not take a status yet; attach it either way.
    try:
        return NespressoError(message, status)
    except TypeError:
        err = NespressoError(message)
        err.status = status
        return err


class FakeApi:
    """Stands in for NespressoApiClient; tests tweak the class attributes."""

    machines: list[Machine] = []
    rejected: dict[str, int] = {}
    """Machine id -> HTTP status its presence call fails with."""
    info_failing: set[str] = set()
    """Machine ids whose machineInfo call fails with a 404."""
    machine_status: int | None = 1
    calls: dict[str, int] = {}
    """Presence calls keyed by machine id, machineInfo calls by ``info:<id>``."""

    def __init__(self, *, session, tokens, country, device_id, token_updated):
        self.token_updated = token_updated

    async def async_get_personal_info(self):
        raise AssertionError("owner_id is preset")

    async def async_list_machines(self, owner_id):
        return list(type(self).machines)

    async def async_get_presence(self, owner_id, machine_id):
        type(self).calls[machine_id] = type(self).calls.get(machine_id, 0) + 1
        if machine_id in type(self).rejected:
            status = type(self).rejected[machine_id]
            raise _error(f"{status} on .../{machine_id}/presence", status)
        return MachinePresence(connected=True)

    async def async_get_status(self, owner_id, machine_id):
        return MachineStatus(
            sync=True,
            last_reported_update=None,
            reported=ReportedStatus(
                machine_status=type(self).machine_status, error_code=0, error_origin=0
            ),
        )

    async def async_get_machine_info(self, owner_id, machine_id):
        key = f"info:{machine_id}"
        type(self).calls[key] = type(self).calls.get(key, 0) + 1
        if machine_id in type(self).info_failing:
            raise _error(f"404 on .../{machine_id}/machineInfo", 404)
        return MachineInfo(machine_fw="17.3.0")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load the integration from custom_components/."""
    yield


@pytest.fixture
def fake_api():
    FakeApi.machines = [
        Machine(id=LIVE, type="machinesVenusProfile", serial_number="SNLIVE1")
    ]
    FakeApi.rejected = {}
    FakeApi.info_failing = set()
    FakeApi.machine_status = 1
    FakeApi.calls = {}
    with patch(
        "custom_components.nespresso_cloud.coordinator.NespressoApiClient", FakeApi
    ):
        yield FakeApi


@pytest.fixture
def entry(hass):
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Test account",
        data={
            "refresh_token": "r0",
            "access_token": "a0",
            "access_token_expires_at": 9_999_999_999,
            "country": "au",
            "owner_id": "1",
            "device_id": "dev",
        },
        options={},
    )
    e.add_to_hass(hass)
    return e


async def setup_integration(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
