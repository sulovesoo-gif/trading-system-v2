"""Render the unapproved 7C-1 state without importing any broker client."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.smoke_gate import ResolvedSmokeConfig


if __name__ == "__main__":
    # No operational approval values are supplied by default. This must remain
    # blocked until all four values are supplied in a future explicit approval.
    print(ResolvedSmokeConfig(None, None, None, None, None).render())
