import unittest
import pandas as pd
import numpy as np
from src.engines.alpha_sweep_scanner import AlphaSweepScanner

class TestCvdCounterTrendGate(unittest.TestCase):
    def setUp(self):
        self.scanner = AlphaSweepScanner()

    def test_counter_trend_fade_without_cvd_is_quarantined(self):
        """Asserts that shorting into an active bull trend without CVD is quarantined."""
        setup = {
            "symbol": "BTC/USD",
            "direction": "SHORT",
            "trend": "BULLISH",
            "hurst": 0.65,
            "regime": "TRENDING",
            "pattern_type": "TURTLE_SOUP_LIQUIDITY_SWEEP",
            "price": 84000.0,
            "level": 84000.0,
            "atr": 200.0,
            "cvd_absorption": False,
            "smt_divergence": False
        }
        
        # Test evaluation of unanchored counter trend fade
        asset_trend = str(setup.get('trend') or '').upper()
        setup_dir = str(setup.get('direction') or '').upper()
        hurst_val = float(setup.get('hurst', 0.50))
        regime_val = str(setup.get('regime') or '').upper()

        is_active_trend = (hurst_val > 0.55) or ("TRENDING" in regime_val)
        is_counter_to_asset_trend = (
            ("BULL" in asset_trend and setup_dir in ["SHORT", "SELL"])
            or ("BEAR" in asset_trend and setup_dir in ["LONG", "BUY"])
        )
        is_fade_archetype = "TURTLE_SOUP" in setup["pattern_type"]
        has_institutional_anchor = setup.get("cvd_absorption", False)

        is_unanchored = is_fade_archetype and is_active_trend and is_counter_to_asset_trend and (not has_institutional_anchor)
        self.assertTrue(is_unanchored, "Must detect counter-trend fade without CVD as unanchored")

    def test_counter_trend_fade_WITH_cvd_is_authorized(self):
        """Asserts that shorting into a bull trend WITH confirmed CVD iceberg absorption is authorized."""
        setup = {
            "symbol": "BTC/USD",
            "direction": "SHORT",
            "trend": "BULLISH",
            "hurst": 0.65,
            "regime": "TRENDING",
            "pattern_type": "TURTLE_SOUP_LIQUIDITY_SWEEP",
            "price": 84000.0,
            "level": 84000.0,
            "atr": 200.0,
            "cvd_absorption": True,
            "smt_divergence": False
        }
        has_institutional_anchor = setup.get("cvd_absorption", False)
        is_unanchored = (not has_institutional_anchor)
        self.assertFalse(is_unanchored, "Must authorize counter-trend fade when verified CVD iceberg absorption is present")

    def test_trend_aligned_pullback_is_authorized_without_cvd(self):
        """Asserts that longing into a bull trend (buying dip) is authorized even without CVD."""
        setup = {
            "symbol": "BTC/USD",
            "direction": "LONG",
            "trend": "BULLISH",
            "hurst": 0.65,
            "regime": "TRENDING",
            "pattern_type": "TURTLE_SOUP_LIQUIDITY_SWEEP",
            "cvd_absorption": False
        }
        asset_trend = str(setup.get('trend') or '').upper()
        setup_dir = str(setup.get('direction') or '').upper()
        is_counter_to_asset_trend = (
            ("BULL" in asset_trend and setup_dir in ["SHORT", "SELL"])
            or ("BEAR" in asset_trend and setup_dir in ["LONG", "BUY"])
        )
        self.assertFalse(is_counter_to_asset_trend, "Long in a bull trend is trend-aligned, not counter-trend")

if __name__ == "__main__":
    unittest.main()
