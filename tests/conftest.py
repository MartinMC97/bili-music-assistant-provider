"""Pytest bootstrap: make ``provider/bilibili`` importable as top-level ``bilibili``."""

from __future__ import annotations

import sys
from pathlib import Path

PROVIDER_ROOT = Path(__file__).resolve().parents[1] / "provider"
if str(PROVIDER_ROOT) not in sys.path:
    sys.path.insert(0, str(PROVIDER_ROOT))
