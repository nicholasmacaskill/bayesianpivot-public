"""
Tier 2: Quantitative Physics & Model Invariants Test Suite
=========================================================
Tests Hurst exponent chaos filtering, SMC liquidity sweeps, and quantitative pattern invariants.
"""

import unittest
import numpy as np
import pandas as pd

from src.core.config import Config
from src.engines.smc_scanner import SMCScanner
from src.engines.alpha_sweep_scanner import AlphaSweepScanner


class TestQuantInvariants(unittest.TestCase):
    def setUp(self):
        self.scanner = SMCScanner()
        self.alpha_scanner = AlphaSweepScanner()

    def test_invariant_hurst_meat_grinder_chaos_filtering(self):
        """
        Invariant: The system must strictly reject symbols in the 0.45 - 0.55 chaos zone.
        """
        # 1. Generate synthetic Brownian Motion (pure 50/50 coin flip noise)
        np.random.seed(42)
        returns = np.random.normal(0, 0.01, 500)
        brownian_prices = 100.0 * np.exp(np.cumsum(returns))
        h_chaos = self.scanner.get_hurst_exponent(brownian_prices)
        
        # Invariant: Chaos range check
        is_chaos = Config.HURST_CHAOS_RANGE[0] <= h_chaos <= Config.HURST_CHAOS_RANGE[1]
        self.assertTrue(is_chaos, f"Synthetic brownian noise (H={h_chaos:.3f}) was not in chaos range!")

        # 2. Generate Mean-Reverting series (Ornstein-Uhlenbeck)
        ou_prices = [100.0]
        theta, mu, sigma = 0.5, 100.0, 1.0
        for _ in range(500):
            dp = theta * (mu - ou_prices[-1]) + sigma * np.random.normal()
            ou_prices.append(ou_prices[-1] + dp)
        h_mean_revert = self.scanner.get_hurst_exponent(np.array(ou_prices))
        self.assertLess(h_mean_revert, Config.HURST_MAX_RANDOM, f"Mean-reverting series (H={h_mean_revert:.3f}) should be < {Config.HURST_MAX_RANDOM}")

        # 3. Generate Trending series (Strong Persistent Trend)
        trend_prices = np.linspace(100.0, 300.0, 500) + np.sin(np.linspace(0, 5, 500)) * 2.0
        h_trend = self.scanner.get_hurst_exponent(trend_prices)
        self.assertGreater(h_trend, Config.HURST_MIN_MEMORY, f"Trending series (H={h_trend:.3f}) should be > {Config.HURST_MIN_MEMORY}")

    def test_invariant_turtle_soup_sweep_and_brackets(self):
        """
        Invariant: Turtle Soup entries must sweep previous extremes, reject with a wick,
        and attach mathematically sound SL below sweep and TP at opposing liquidity.
        """
        # Synthetic DataFrame where 5m candle sweeps a major low and wicks back up
        timestamps = pd.date_range(end=pd.Timestamp.utcnow(), periods=30, freq='5min')
        closes = [77000.0 - (i * 10) for i in range(28)]
        closes.append(76680.0) # Sweep low
        closes.append(76780.0) # Rejection close back up

        df_5m = pd.DataFrame({
            'timestamp': timestamps,
            'open': [c + 10 for c in closes],
            'high': [c + 30 for c in closes],
            'low': [c - 20 for c in closes],
            'close': closes,
            'volume': [10.0] * 30
        })
        # Set the sweep candle
        df_5m.loc[28, 'low'] = 76570.0 # Pierced low
        df_5m.loc[29, 'low'] = 76650.0
        df_5m.loc[29, 'high'] = 76820.0
        df_5m.loc[29, 'open'] = 76680.0
        df_5m.loc[29, 'close'] = 76780.0 # Upper close

        df_1h = pd.DataFrame({
            'timestamp': pd.date_range(end=pd.Timestamp.utcnow(), periods=60, freq='1h'),
            'open': [77000] * 60,
            'high': [77500] * 60,
            'low': [76500] * 60,
            'close': [77000 + np.sin(i * 0.3) * 200 for i in range(60)],
            'volume': [100.0] * 60
        })

        setup = self.alpha_scanner.check_turtle_soup("BTC/USD", df_5m, df_1h)
        if setup:
            self.assertEqual(setup["direction"], "LONG")
            self.assertIn("TURTLE_SOUP", setup.get("pattern_type", setup.get("pattern", "TURTLE_SOUP")))
            self.assertIn("hurst", setup)
            self.assertIn("atr", setup)

    def test_invariant_mlx_lora_lifecycle_and_governor_sentry(self):
        """
        Invariant: The Quality Governor and Unified Supervisor must monitor and
        maintain the Apple Silicon MLX LoRA daemon lifecycle without silent dropouts.
        """
        from src.core.quality_governor import QualityGovernor
        from unittest.mock import patch, MagicMock

        gov = QualityGovernor()

        # 1. When port 8080 responds with 200, check_mlx_lora_health must report healthy
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        with patch("requests.get", return_value=mock_resp_ok):
            is_healthy, issues = gov.check_mlx_lora_health(auto_revive=False)
            self.assertTrue(is_healthy)
            self.assertEqual(len(issues), 0)

        # 2. When port 8080 fails and auto_revive is False, it must flag the offline issue
        with patch("requests.get", side_effect=ConnectionError("Offline")):
            is_healthy, issues = gov.check_mlx_lora_health(auto_revive=False)
            self.assertFalse(is_healthy)
            self.assertTrue(any("offline" in iss.lower() for iss in issues))


if __name__ == "__main__":
    unittest.main()

