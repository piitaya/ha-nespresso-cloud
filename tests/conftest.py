"""Shared fixtures for the Nespresso Cloud tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load the integration from custom_components/."""
    yield
