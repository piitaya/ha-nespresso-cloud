"""Run the test suite: ``python scripts/test.py [pytest args]``.

The tests set up the integration in a real Home Assistant test instance with
the Nespresso API faked, so no account is needed.

Home Assistant doesn't support Windows, and its test plugin imports two
Unix-only modules as pytest loads it. On Windows this puts stand-ins for them
(tests/winshim) on the path first; elsewhere it just runs pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    sys.path.insert(0, str(REPO_ROOT / "tests" / "winshim"))

sys.exit(pytest.main(["-c", str(REPO_ROOT / "pytest.ini"), *sys.argv[1:]]))
