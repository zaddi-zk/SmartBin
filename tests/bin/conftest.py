"""Make the legacy bot importable for the section 17 port tests."""

from __future__ import annotations

import sys
from pathlib import Path

_SMARTBIN = Path(__file__).resolve().parents[2] / "smartbin"
if str(_SMARTBIN) not in sys.path:
    sys.path.insert(0, str(_SMARTBIN))
