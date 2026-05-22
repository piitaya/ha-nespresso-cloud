"""Sensor entities for the Nespresso Cloud integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NespressoCloudEntry
from .coordinator import MachineSnapshot, NespressoCoordinator
from .entity import NespressoCloudEntity
from .lib.api import (
    ERROR_CODE_LABELS,
    ERROR_ORIGIN_LABELS,
    MACHINE_STATUS_LABELS,
    MILK_UNIT_STATUS_LABELS,
    error_code_label,
    error_origin_label,
    machine_status_label,
    milk_unit_status_label,
)


@dataclass(frozen=True, kw_only=True)
class NespressoSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[MachineSnapshot], Any]


def _machine_state(snap: MachineSnapshot) -> str | None:
    if snap.status is None:
        return None
    return machine_status_label(snap.status.reported.machine_status)


def _last_seen(snap: MachineSnapshot) -> Any:
    if snap.presence and snap.presence.last_update:
        return snap.presence.last_update
    if snap.status and snap.status.last_reported_update:
        return snap.status.last_reported_update
    return None


def _milk_unit(snap: MachineSnapshot) -> str | None:
    if snap.status is None:
        return None
    return milk_unit_status_label(snap.status.reported.milk_unit_status)


def _error(snap: MachineSnapshot) -> str | None:
    if snap.status is None:
        return None
    return error_code_label(snap.status.reported.error_code)


def _error_code(snap: MachineSnapshot) -> int | None:
    if snap.status is None:
        return None
    return snap.status.reported.error_code


def _error_origin(snap: MachineSnapshot) -> str | None:
    if snap.status is None:
        return None
    return error_origin_label(snap.status.reported.error_origin)


# Derive ENUM options from the label dicts so they stay in sync.
MACHINE_STATE_OPTIONS = sorted(set(MACHINE_STATUS_LABELS.values()))
MILK_UNIT_OPTIONS = sorted(set(MILK_UNIT_STATUS_LABELS.values()))
ERROR_ORIGIN_OPTIONS = sorted(set(ERROR_ORIGIN_LABELS.values()))
ERROR_CODE_OPTIONS = sorted(set(ERROR_CODE_LABELS.values()))


SENSOR_DESCRIPTIONS: tuple[NespressoSensorDescription, ...] = (
    NespressoSensorDescription(
        key="machine_state",
        translation_key="machine_state",
        name=None,
        device_class=SensorDeviceClass.ENUM,
        options=MACHINE_STATE_OPTIONS,
        value_fn=_machine_state,
    ),
    NespressoSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_seen,
    ),
    NespressoSensorDescription(
        key="milk_unit",
        translation_key="milk_unit",
        device_class=SensorDeviceClass.ENUM,
        options=MILK_UNIT_OPTIONS,
        value_fn=_milk_unit,
    ),
    NespressoSensorDescription(
        key="error",
        translation_key="error",
        device_class=SensorDeviceClass.ENUM,
        options=ERROR_CODE_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error,
    ),
    NespressoSensorDescription(
        key="error_code",
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error_code,
        entity_registry_enabled_default=False,
    ),
    NespressoSensorDescription(
        key="error_origin",
        translation_key="error_origin",
        device_class=SensorDeviceClass.ENUM,
        options=ERROR_ORIGIN_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error_origin,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NespressoCloudEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for each machine on the account."""
    coordinator = entry.runtime_data
    machines = coordinator.data.machines if coordinator.data else {}
    entities: list[NespressoSensor] = []
    for machine_id, snap in machines.items():
        # ``1Plus1`` profile codes (e.g. ``machinesVenus1Plus1DLProfile``)
        # are the 1+1 models — brewer plus milk frother. Other Vertuo
        # types don't have one and would just report ``offline`` forever.
        has_milk_frother = "1Plus1" in snap.machine.type
        for desc in SENSOR_DESCRIPTIONS:
            if desc.key == "milk_unit" and not has_milk_frother:
                continue
            entities.append(NespressoSensor(coordinator, machine_id, desc))
    async_add_entities(entities)


class NespressoSensor(NespressoCloudEntity, SensorEntity):
    """Sensor entity reading from the per-machine snapshot."""

    entity_description: NespressoSensorDescription

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        machine_id: str,
        description: NespressoSensorDescription,
    ) -> None:
        """Initialize the sensor for one machine."""
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the current sensor value, or None if unknown."""
        snap = self._snapshot
        if snap is None:
            return None
        return self.entity_description.value_fn(snap)
