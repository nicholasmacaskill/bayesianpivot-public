#!/usr/bin/env python3
"""
================================================================================
⚡ SOVEREIGN 3D CRUCIBLE MATRIX // MULTI-AXIS ADVERSARIAL ENGINE
================================================================================
Entrypoint for the 8-tier multi-dimensional invariant, fuzzing, and mutation
meta-testing engine.

Axes of Verification:
  • Axis X: Mathematical Invariants (Lot sizing, price geometry, contract multipliers)
  • Axis Y: Temporal Dynamics & State (Trailing defense, daemon reboot persistence)
  • Axis Z: Hostile Environmental Chaos (Broker 429s, network drops, fill slippage)
================================================================================
"""

import sys
import os

# Ensure root directory is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Delegate directly to the master harness runner
from tests.run_bulletproof_harness import main

if __name__ == "__main__":
    main()
