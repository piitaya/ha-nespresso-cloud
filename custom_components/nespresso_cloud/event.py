"""Event entities for the Nespresso Cloud integration."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NespressoCloudEntry
from .const import EVENT_TYPES
from .coordinator import NespressoCoordinator
from .entity import NespressoCloudEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NespressoCloudEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one event entity per machine on the account."""
    coordinator = entry.runtime_data
    machines = coordinator.data.machines if coordinator.data else {}
    async_add_entities(
        NespressoMachineEvent(coordinator, machine_id) for machine_id in machines
    )


class NespressoMachineEvent(NespressoCloudEntity, EventEntity):
    """Fires on brew, error, tank and descaling transitions between polls."""

    _attr_translation_key = "machine_event"
    _attr_event_types = list(EVENT_TYPES)

    def __init__(self, coordinator: NespressoCoordinator, machine_id: str) -> None:
        """Initialize the event entity for one machine."""
        super().__init__(coordinator, machine_id, "machine_event")

    @property
    def available(self) -> bool:
        """Stay available offline so the last event stays visible."""
        return self._snapshot is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire every event detected since the last refresh."""
        events = self._new_events()
        if not events:
            super()._handle_coordinator_update()
            return
        # One write per event, or same-poll events collapse into the last.
        for event in events:
            self._trigger_event(event.type, event.data)
            self.async_write_ha_state()
