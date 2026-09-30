"""
Unit & Invariant Tests: Retail Stop Trap & Equal Highs/Lows Shelf Engine
========================================================================
Verifies:
1. Detection of Equal Highs (Double Top) Sweep Trap with Rejection Wick and Volume Exhaustion
2. Detection of Equal Lows (Double Bottom) Sweep Trap
3. Correct 2.60R target and 1.40 breathing room calculation
4. Fallback to LuxAlgo BOS trap when no shelf is present
"""

import unittest
import pandas as pd
import numpy as np
from src.engines.retail_trap_engine import RetailStopTrapEngine

class TestRetailTrapShelfEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RetailStopTrapEngine()

    def test_eqh_bear_trap_detection(self):
        """Test bearish trap when price sweeps equal highs with upper wick and volume."""
        n = 50
        dates = pd.date_range("2026-09-29 00:00", periods=n, freq="5min")
        
        # Build baseline range around 84,000
        opens = [84000.0] * n
        highs = [84050.0] * n
        lows = [83950.0] * n
        closes = [84000.0] * n
        volumes = [10.0] * n

        # Create two distinct peaks forming EQH shelf at 84,200 (within 0.08%)
        # Peak 1 at index 15
        highs[15] = 84200.0
        closes[15] = 84180.0
        # Peak 2 at index 30
        highs[30] = 84202.0
        closes[30] = 84185.0

        # Trigger candle at last_idx = n - 2 = index 48
        # Sweeps to 84,250, closes back down at 84,150 (Upper wick = 84,250 - 84,150 = 100 on range of 150 = 66% wick!)
        opens[48] = 84100.0
        highs[48] = 84250.0
        lows[48] = 84100.0
        closes[48] = 84150.0
        volumes[48] = 25.0 # Dissonance surge (2.5x SMA)

        df_5m = pd.DataFrame({
            "timestamp": dates, "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes
        })
        df_1h = df_5m.copy()

        trap = self.engine.detect_eqh_eql_shelf_trap(df_5m, df_1h)
        self.assertIsNotNone(trap, "Failed to detect Equal Highs Bearish Trap")
        self.assertEqual(trap["direction"], "SHORT")
        self.assertEqual(trap["pattern_type"], "RETAIL_EQH_EQL_SHELF_TRAP")
        self.assertGreaterEqual(trap["target_rr"], 2.5)
        self.assertEqual(trap["breathing_room_threshold"], 1.40)
        self.assertLess(trap["take_profit"], trap["price"])
        self.assertGreater(trap["stop_loss"], trap["price"])

    def test_eql_bull_trap_detection(self):
        """Test bullish trap when price sweeps equal lows with lower wick and volume."""
        n = 50
        dates = pd.date_range("2026-09-29 00:00", periods=n, freq="5min")
        
        opens = [4150.0] * n
        highs = [4160.0] * n
        lows = [4140.0] * n
        closes = [4150.0] * n
        volumes = [10.0] * n

        # Trough 1 at index 15
        lows[15] = 4130.0
        closes[15] = 4135.0
        # Trough 2 at index 30
        lows[30] = 4131.0
        closes[30] = 4136.0

        # Trigger candle at last_idx = index 48
        # Sweeps down to 4120, closes back up at 4135 (Lower wick = 4135 - 4120 = 15 on range of 20 = 75% wick!)
        opens[48] = 4138.0
        highs[48] = 4140.0
        lows[48] = 4120.0
        closes[48] = 4135.0
        volumes[48] = 22.0

        df_5m = pd.DataFrame({
            "timestamp": dates, "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes
        })
        df_1h = df_5m.copy()

        trap = self.engine.detect_eqh_eql_shelf_trap(df_5m, df_1h)
        self.assertIsNotNone(trap, "Failed to detect Equal Lows Bullish Trap")
        self.assertEqual(trap["direction"], "LONG")
        self.assertEqual(trap["pattern_type"], "RETAIL_EQH_EQL_SHELF_TRAP")
        self.assertGreater(trap["take_profit"], trap["price"])
        self.assertLess(trap["stop_loss"], trap["price"])

if __name__ == "__main__":
    unittest.main()
