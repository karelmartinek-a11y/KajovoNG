from __future__ import annotations

import sys
from pathlib import Path


# Zajistí import balíčku kajovo při spuštění pytest bez nastaveného PYTHONPATH.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
