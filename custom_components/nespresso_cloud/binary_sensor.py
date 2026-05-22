"""Binary-sensor entities for the Nespresso Cloud integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NespressoCloudEntry
from .const import (
    MACHINE_STATUS_OLD_CAPSULE_DETECTED,
    MACHINE_STATUS_TANK_EMPTY,
)
from .coordinator import MachineSnapshot, NespressoCoordinator
from .entity import NespressoCloudEntity


@dataclass(frozen=True, kw_only=True)
class NespressoBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor."""

    value_fn: Callable[[MachineSnapshot], bool | None]
    """Return ``None`` to mark the state as unknown."""


def _connected(snap: MachineSnapshot) -> bool | None:
    if snap.presence is None:
        return None
    return snap.presence.connected


def _descaling_alert(snap: MachineSnapshot) -> bool | None:
    if snap.status is None:
        return None
    return snap.status.reported.descaling_alert


def _tank_empty(snap: MachineSnapshot) -> bool | None:
    if snap.status is None:
        return None
    code = snap.status.reported.machine_status
    if code is None:
        return None
    return code == MACHINE_STATUS_TANK_EMPTY


def _old_capsule(snap: MachineSnapshot) -> bool | None:
    if snap.status is None:
        return None
    code = snap.status.reported.machine_status
    if code is None:
        return None
    return code == MACHINE_STATUS_OLD_CAPSULE_DETECTED


BINARY_SENSOR_DESCRIPTIONS: tuple[NespressoBinarySensorDescription, ...] = (
    NespressoBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_connected,
    ),
    NespressoBinarySensorDescription(
        key="descaling_alert",
        translation_key="descaling_alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_descaling_alert,
    ),
    NespressoBinarySensorDescription(
        key="tank_empty",
        translation_key="tank_empty",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_tank_empty,
    ),
    NespressoBinarySensorDescription(
        key="old_capsule",
        translation_key="old_capsule",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_old_capsule,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NespressoCloudEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities for each machine on the account."""
    coordinator = entry.runtime_data
    machines = coordinator.data.machines if coordinator.data else {}
    entities: list[NespressoBinarySensor] = []
    for machine_id in machines:
        for desc in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(NespressoBinarySensor(coordinator, machine_id, desc))
    async_add_entities(entities)


class NespressoBinarySensor(NespressoCloudEntity, BinarySensorEntity):
    """Binary sensor entity reading from the per-machine snapshot."""

    entity_description: NespressoBinarySensorDescription

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        machine_id: str,
        description: NespressoBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor for one machine."""
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True/False/None for the binary sensor's current state."""
        snap = self._snapshot
        if snap is None:
            return None
        return self.entity_description.value_fn(snap)
