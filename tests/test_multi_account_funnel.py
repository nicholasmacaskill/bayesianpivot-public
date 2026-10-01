import sys
import os
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.database import init_db, get_db_connection
from src.engines.multi_account_funnel import MultiAccountFunnelManager, AccountValidatorProfile
from src.engines.counterfactual_tracker import CounterfactualTracker

class TestMultiAccountFunnel(unittest.TestCase):
    def setUp(self):
        init_db()
        self.manager = MultiAccountFunnelManager()
        self.tracker = CounterfactualTracker()
        conn = get_db_connection()
        conn.execute("DELETE FROM counterfactual_trades WHERE account_key = 'ACCOUNT_A'")
        conn.commit()
        conn.close()

    def test_profile_initialization(self):
        self.assertEqual(len(self.manager.profiles), 9)
        self.assertIn("ACCOUNT_A", self.manager.profiles)
        self.assertIn("ACCOUNT_B", self.manager.profiles)
        self.assertIn("ACCOUNT_C", self.manager.profiles)
        self.assertIn("ACCOUNT_D", self.manager.profiles)
        self.assertIn("ACCOUNT_E", self.manager.profiles)
        self.assertIn("ACCOUNT_F", self.manager.profiles)
        self.assertIn("ACCOUNT_G", self.manager.profiles)
        self.assertIn("ACCOUNT_H", self.manager.profiles)
        self.assertIn("ACCOUNT_I", self.manager.profiles)




    def test_account_a_strict_filter(self):
        setup = {"symbol": "BTC/USD", "direction": "BUY", "price": 65000.0, "stop_loss": 64500.0, "take_profit": 66500.0}
        # Hurst inside 0.45-0.55 chaos range -> should fail Account A
        passed, reasons = self.manager.evaluate_setup_for_account(
            setup=setup, account_key="ACCOUNT_A", hurst=0.50, ai_score=8.5, cal_safe=True
        )
        self.assertFalse(passed)
        self.assertTrue(any("HURST_CHAOS_GATE" in r for r in reasons))

    def test_account_d_reversal_filter(self):
        setup = {"symbol": "ETH/USD", "direction": "BUY", "price": 3000.0, "stop_loss": 2950.0, "take_profit": 3150.0}
        # Account D requires Hurst <= 0.45 (REVERSAL_ONLY), ai_score >= 7.5, smt_strength >= 0.15
        passed, reasons = self.manager.evaluate_setup_for_account(
            setup=setup, account_key="ACCOUNT_D", hurst=0.40, smt_strength=0.25, ai_score=8.0, cal_safe=True, corr_ok=True, regime_allowed=True
        )
        self.assertTrue(passed)
        self.assertEqual(len(reasons), 0)

    def test_anti_hedging_gate(self):
        setup_buy = {"symbol": "BTC/USD", "direction": "BUY", "price": 65000.0, "stop_loss": 64500.0, "take_profit": 66500.0}
        existing_positions = [{"symbol": "BTC/USD", "side": "SELL"}]

        # Evaluating BUY on BTC/USD when SELL on BTC/USD exists -> MUST FAIL
        passed, reasons = self.manager.evaluate_setup_for_account(
            setup=setup_buy, account_key="ACCOUNT_B", hurst=0.60, smt_strength=0.25, ai_score=8.5, cal_safe=True, corr_ok=True, regime_allowed=True, open_positions=existing_positions
        )
        self.assertFalse(passed)
        self.assertTrue(any("ANTI_HEDGE_POSITION_BLOCKED" in r for r in reasons))



    def test_counterfactual_registration_and_summary(self):
        setup = {"symbol": "SOL/USD", "direction": "BUY", "price": 140.0, "stop_loss": 135.0, "take_profit": 150.0, "pattern": "TestFVG"}
        reasons = ["HURST_CHAOS_GATE (0.500)", "AI_SCORE_BELOW_THRESHOLD (6.0 < 8.0)"]
        
        # Register shadow trade
        success = self.tracker.register_shadow_trade(setup, "ACCOUNT_A", "CONSERVATIVE", reasons)
        self.assertTrue(success)

        # Check DB entry
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM counterfactual_trades WHERE symbol='SOL/USD' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["account_key"], "ACCOUNT_A")
        self.assertEqual(row["status"], "OPEN")

        # Summary check
        summary = self.tracker.get_counterfactual_summary()
        self.assertGreater(summary.get("total_shadow_trades", 0), 0)

if __name__ == "__main__":
    unittest.main()
