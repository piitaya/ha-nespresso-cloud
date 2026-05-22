"""Nespresso ECapi client — pure Python, no HA dependency."""

from __future__ import annotations

from .api import (
    NespressoApiClient,
    NespressoAuthError,
    NespressoError,
    async_exchange_authorization_code,
)
from .models import (
    Machine,
    MachineInfo,
    MachineModule,
    MachinePresence,
    MachineStatus,
    PersonalInfo,
    ReportedStatus,
    Tokens,
)

__all__ = [
    "Machine",
    "MachineInfo",
    "MachineModule",
    "MachinePresence",
    "MachineStatus",
    "NespressoApiClient",
    "NespressoAuthError",
    "NespressoError",
    "PersonalInfo",
    "ReportedStatus",
    "Tokens",
    "async_exchange_authorization_code",
]
