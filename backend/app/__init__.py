"""Legacy import path forwarding to the Core Application Service."""

import sys
from pathlib import Path

_core_src = Path(__file__).resolve().parents[1] / "services/core_application_service/src"
if str(_core_src) not in sys.path:
    sys.path.insert(0, str(_core_src))
