"""Constants for the Nespresso Cloud integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nespresso_cloud"

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_ACCESS_TOKEN_EXPIRES_AT: Final = "access_token_expires_at"
CONF_COUNTRY: Final = "country"
CONF_OWNER_ID: Final = "owner_id"
CONF_DEVICE_ID: Final = "device_id"

# Polling cadence (seconds). Across multiple machines, fastest wins.
SCAN_INTERVAL_OFFLINE_S: Final = 60
SCAN_INTERVAL_IDLE_S: Final = 20
SCAN_INTERVAL_ACTIVE_S: Final = 5

MACHINE_STATUS_TANK_EMPTY: Final = 19
MACHINE_STATUS_OLD_CAPSULE_DETECTED: Final = 35

# `*_READY` and `*_PAUSED` states persist until the user acts, so don't
# count as "active" — polling fast there wastes calls.
ACTIVE_MACHINE_STATUS_CODES: Final = frozenset(
    {
        1,    # HEAT_UP_IN_PROGRESS
        4,    # BREWING_IN_PROGRESS
        5,    # CLEANING_IN_PROGRESS
        6,    # DESCALING_IN_PROGRESS
        7,    # EMPTYING_IN_PROGRESS
        10,   # COOL_DOWN  (short, transient)
        13,   # UPDATING
        14,   # RINSING_IN_PROGRESS
        17,   # CAPSULE_READING
        18,   # DESCALE_SEQUENCE_DECODING
        21,   # INITIALIZATION
        100,  # HARD_RESET_RUNNING
        101,  # SOFT_RESET_RUNNING
    }
)

