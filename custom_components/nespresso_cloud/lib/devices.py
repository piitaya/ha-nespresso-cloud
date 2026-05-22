"""Real Android device descriptors used to build per-install User-Agents."""

from __future__ import annotations

import hashlib

# (Android version, Build.MANUFACTURER, Build.MODEL)
DEVICE_DESCRIPTORS: tuple[tuple[str, str, str], ...] = (
    # OnePlus
    ("Android/13", "OnePlus", "IN2013"),        # OnePlus 8T 5G
    ("Android/13", "OnePlus", "LE2113"),        # OnePlus 9
    ("Android/13", "OnePlus", "LE2125"),        # OnePlus 9 Pro
    ("Android/13", "OnePlus", "NE2213"),        # OnePlus 10 Pro
    ("Android/14", "OnePlus", "CPH2449"),       # OnePlus 11
    ("Android/14", "OnePlus", "CPH2581"),       # OnePlus 12
    ("Android/14", "OnePlus", "CPH2691"),       # OnePlus 12R
    ("Android/13", "OnePlus", "CPH2399"),       # OnePlus Nord 2T 5G
    # Samsung Galaxy
    ("Android/13", "samsung", "SM-G991B"),      # Galaxy S21
    ("Android/13", "samsung", "SM-G998B"),      # Galaxy S21 Ultra
    ("Android/13", "samsung", "SM-S911B"),      # Galaxy S23
    ("Android/13", "samsung", "SM-S918B"),      # Galaxy S23 Ultra
    ("Android/14", "samsung", "SM-S921B"),      # Galaxy S24
    ("Android/14", "samsung", "SM-S926B"),      # Galaxy S24+
    ("Android/14", "samsung", "SM-S928B"),      # Galaxy S24 Ultra
    ("Android/13", "samsung", "SM-A536B"),      # Galaxy A53 5G
    ("Android/14", "samsung", "SM-A546B"),      # Galaxy A54 5G
    ("Android/14", "samsung", "SM-A556B"),      # Galaxy A55 5G
    ("Android/14", "samsung", "SM-F946B"),      # Galaxy Z Fold5
    ("Android/14", "samsung", "SM-F731B"),      # Galaxy Z Flip5
    # Google Pixel
    ("Android/12", "Google", "Pixel 6"),
    ("Android/13", "Google", "Pixel 6a"),
    ("Android/13", "Google", "Pixel 7"),
    ("Android/13", "Google", "Pixel 7 Pro"),
    ("Android/13", "Google", "Pixel 7a"),
    ("Android/14", "Google", "Pixel 8"),
    ("Android/14", "Google", "Pixel 8 Pro"),
    ("Android/14", "Google", "Pixel 8a"),
    # Xiaomi / Redmi / POCO
    ("Android/13", "Xiaomi", "2201123G"),       # Xiaomi 12
    ("Android/13", "Xiaomi", "2210132G"),       # Xiaomi 13
    ("Android/14", "Xiaomi", "23117RA68G"),     # Redmi Note 13 Pro 5G
    ("Android/13", "Xiaomi", "2312DRA50G"),     # POCO X6 Pro 5G
    # Motorola
    ("Android/13", "motorola", "XT2243-1"),     # Edge 30 Pro
    ("Android/14", "motorola", "XT2301-5"),     # Edge 40 Neo
    # OPPO
    ("Android/13", "OPPO", "CPH2363"),          # Find X5 Pro
    ("Android/14", "OPPO", "CPH2491"),          # Reno 10 Pro+
    # Sony
    ("Android/13", "Sony", "XQ-CT54"),          # Xperia 1 IV
    ("Android/14", "Sony", "XQ-DQ54"),          # Xperia 1 V
    # Nothing
    ("Android/13", "Nothing", "A063"),          # Phone (1)
    ("Android/14", "Nothing", "A065"),          # Phone (2)
)


def descriptor_for(device_id: str) -> tuple[str, str, str]:
    """Return a stable descriptor for ``device_id``."""
    digest = hashlib.sha1(device_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(DEVICE_DESCRIPTORS)
    return DEVICE_DESCRIPTORS[index]
