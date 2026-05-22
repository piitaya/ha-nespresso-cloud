"""Base entity for the Nespresso Cloud integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MachineSnapshot, NespressoCoordinator
from .lib import Machine

# Profile-code (`machine.type`) → human marketing name.
MODEL_BY_PROFILE: dict[str, str] = {
    "machinesVenusProfile": "Vertuo Next",
    "machinesVenusOneProfile": "Vertuo Pop",
    "machinesVenusMiniProfile": "Vertuo Up",
    "machinesVenusMoonProfile": "Vertuo Pop+",
    "machinesVenus1Plus1BRProfile": "Vertuo Creatista",
    "machinesVenus1Plus1DLProfile": "Vertuo Lattissima",
}


class NespressoCloudEntity(CoordinatorEntity[NespressoCoordinator]):
    """Base class for sensors/binary sensors tied to a single machine."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        machine_id: str,
        key: str,
    ) -> None:
        """Initialize the entity with its machine id and key."""
        super().__init__(coordinator)
        self._machine_id = machine_id
        self._attr_unique_id = f"{machine_id}_{key}"

    @property
    def _snapshot(self) -> MachineSnapshot | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.machines.get(self._machine_id)

    @property
    def available(self) -> bool:
        """Return True when the coordinator has a snapshot for this machine."""
        return super().available and self._snapshot is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Build the device info from the live snapshot.

        Computed dynamically so rename/firmware/serial changes on the
        Nespresso side propagate without an HA reload.
        """
        snap = self._snapshot
        machine = snap.machine if snap else None
        product_id = machine.product_id if machine else None
        # Strip the catalog prefix so the displayed model_id is the SKU
        # users see on packaging (e.g. ``ENV300W``), not ``erp.fr.b2c/prod/ENV300W``.
        model_id = product_id.split("/")[-1] if product_id else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._machine_id)},
            name=(machine.custom_name if machine and machine.custom_name else None)
            or self._default_name(machine),
            manufacturer="Nespresso",
            model=MODEL_BY_PROFILE.get(machine.type, machine.type) if machine else None,
            model_id=model_id,
            serial_number=machine.serial_number if machine else None,
            sw_version=snap.info.machine_fw if snap and snap.info else None,
        )

    def _default_name(self, machine: Machine | None) -> str:
        if not machine:
            return "Nespresso Machine"
        suffix = (machine.serial_number or machine.id)[-6:]
        model = MODEL_BY_PROFILE.get(machine.type, "Nespresso")
        return f"{model} {suffix}"
