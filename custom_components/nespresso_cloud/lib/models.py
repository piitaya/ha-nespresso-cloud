"""Typed value objects for the Nespresso ECapi responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Tokens:
    """OAuth tokens we hold for one config entry."""

    access_token: str
    refresh_token: str
    expires_at: float
    """Epoch seconds when ``access_token`` ceases to be valid."""


@dataclass
class PersonalInfo:
    """Subset of /me/personal-info we care about."""

    member_number: str
    """ECapi's ``ownerId`` path segment — a numeric string."""

    language: str | None = None
    currency: str | None = None
    sign_up_date: str | None = None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> PersonalInfo:
        return cls(
            member_number=str(data["memberNumber"]),
            language=data.get("language"),
            currency=data.get("currency"),
            sign_up_date=data.get("signUpDate"),
        )


@dataclass
class MachineModule:
    """A sub-module of a machine (e.g. the milk frother on DV5)."""

    id: str
    """Module's own ID — for DV5 this is the brewer's serial number."""

    type: str
    """e.g. ``"venus1plus1dl"``."""

    machine_id: str

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> MachineModule:
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "")),
            machine_id=str(data.get("machineId", "")),
        )


@dataclass
class Machine:
    """A user-paired machine, as returned by /machines/v1/{country}/{channel}/{owner}."""

    id: str
    """Stable machine UUID. Used as path segment for all per-machine endpoints."""

    type: str
    """Profile code, e.g. ``"machinesVenus1Plus1DLProfile"``."""

    product_id: str | None = None
    serial_number: str | None = None
    custom_name: str | None = None
    pairing_key: str | None = None
    mac_address: str | None = None
    wifi_name: str | None = None
    wifi_mac_address: str | None = None
    purchase_date: str | None = None
    modules: list[MachineModule] = field(default_factory=list)

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> Machine:
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "")),
            product_id=data.get("productId"),
            serial_number=data.get("serialNumber"),
            custom_name=data.get("customName"),
            pairing_key=data.get("pairingKey"),
            mac_address=data.get("macAddress"),
            wifi_name=data.get("wiFiName"),
            wifi_mac_address=data.get("wiFiMacAddress"),
            purchase_date=data.get("purchaseDate"),
            modules=[MachineModule.from_response(m) for m in data.get("modules", [])],
        )


@dataclass
class ReportedStatus:
    """The /status `.reported` payload — everything dynamic about the machine."""

    machine_status: int | None = None
    """See ``MACHINE_STATUS_LABELS`` in api.py for the mapping."""

    error_code: int | None = None
    error_origin: int | None = None
    descaling_alert: bool | None = None
    fota_status: int | None = None
    milk_unit_status: int | None = None
    last_coffee_family_id: int | None = None
    water_hardness: int | None = None
    first_coffee: bool | None = None
    first_rinsing: bool | None = None
    recipe_tag: str | None = None
    volume_customization: str | None = None
    temperature_customization: str | None = None
    firmwares: dict[str, str] = field(default_factory=dict)
    """Flattened ``{NM: FWR}`` view of ``machineInfo``."""

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> ReportedStatus:
        firmwares = {
            str(part["NM"]): str(part.get("FWR", ""))
            for part in data.get("machineInfo", [])
            if part.get("NM")
        }
        return cls(
            machine_status=data.get("machineStatus"),
            error_code=data.get("errorCode"),
            error_origin=data.get("errorOrigin"),
            descaling_alert=data.get("descalingAlert"),
            fota_status=data.get("fotaStatus"),
            milk_unit_status=data.get("milkUnitStatus"),
            last_coffee_family_id=data.get("lastCoffeeFamilyID"),
            water_hardness=data.get("waterHardness"),
            first_coffee=data.get("firstCoffee"),
            first_rinsing=data.get("firstRinsing"),
            recipe_tag=data.get("recipeTag"),
            volume_customization=data.get("volumeCustomization"),
            temperature_customization=data.get("temperatureCustomization"),
            firmwares=firmwares,
        )


@dataclass
class MachineStatus:
    """The full /status response."""

    sync: bool | None
    last_reported_update: datetime | None
    reported: ReportedStatus

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> MachineStatus:
        raw_ts = data.get("lastReportedUpdate")
        last_update: datetime | None = None
        if raw_ts:
            try:
                last_update = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
            except (TypeError, ValueError):
                last_update = None
        return cls(
            sync=data.get("sync"),
            last_reported_update=last_update,
            reported=ReportedStatus.from_response(data.get("reported") or {}),
        )


@dataclass
class MachinePresence:
    """The /presence response — connectivity from AWS IoT's POV.

    Note: the wire format uses PascalCase keys; we normalize here.
    """

    connected: bool
    brand: int | None = None
    machine_id: str | None = None
    """The machine's serial number, NOT the machine UUID."""
    last_update: datetime | None = None
    principal_id: str | None = None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> MachinePresence:
        raw = data.get("LastUpdate")
        last_update: datetime | None = None
        if raw:
            try:
                # Format observed: "2026-05-20 22:14:39.596" — UTC, no tz marker.
                last_update = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                try:
                    last_update = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    last_update = None
        return cls(
            connected=bool(data.get("Connected", False)),
            brand=data.get("Brand"),
            machine_id=data.get("MachineID"),
            last_update=last_update,
            principal_id=data.get("PrincipalID"),
        )


@dataclass
class MachineInfo:
    """The /machineInfo response — flat firmware/brand view."""

    machine_id: str | None = None
    brand: str | None = None
    machine_fw: str | None = None
    connectivity_fw: str | None = None
    recipe_version: str | None = None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> MachineInfo:
        return cls(
            machine_id=data.get("machineid"),
            brand=data.get("brand"),
            machine_fw=data.get("machinefw"),
            connectivity_fw=data.get("connectivityfw"),
            recipe_version=data.get("recipeversion"),
        )
