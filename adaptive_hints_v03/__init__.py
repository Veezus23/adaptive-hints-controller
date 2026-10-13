"""Versioned deterministic regrouping evidence-ledger prototype."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history_v02"
if str(HISTORY) not in sys.path:
    sys.path.insert(0, str(HISTORY))
__version__ = "0.3.1"
