import unittest
import sqlite3
import os
from unittest.mock import patch, MagicMock
from src.engines.execution_shadow_engine import ExecutionShadowEngine
from src.core.database import get_db_connection

class TestExecutionShadowEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionShadowEngine()
        self.engine.init_db()
        # Clean test table
        conn = get_db_connection()
        conn.execute("DELETE FROM execution_shadow_trades WHERE symbol LIKE 'TEST%'")
        conn.commit()
        conn.close()

    def test_full_tp_scenario(self):
        """When a trade hits full TP, live gets 3.0R, partial gets 2.25R, binary gets 3.0R, buffered gets 3.0R."""
        self.engine.register_trade(
            symbol="TEST/BTC",
            direction="SHORT",
            entry_price=100.0,
            stop_loss=110.0,    # 10 pt risk
            take_profit=70.0,   # 30 pt target (3.0R)
            risk_usd=100.0
        )
        
        # Price drops to 69.0 (TP breached)
        resolved = self.engine.update_price("TEST/BTC", 69.0)
        self.assertEqual(len(resolved), 1)
        res = resolved[0]
        self.assertAlmostEqual(res["live_ratchet_r"], 3.0)
        self.assertAlmostEqual(res["shadow_partial_r"], 2.25)
        self.assertAlmostEqual(res["shadow_binary_r"], 3.0)
        self.assertAlmostEqual(res["shadow_buffered_r"], 3.0)

    def test_pullback_after_1_8r_scenario(self):
        """When trade reaches +1.8R and then hits SL, live gets 0.0R (BE), partial gets +0.75R, binary gets -1.0R."""
        self.engine.register_trade(
            symbol="TEST/ETH",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,     # 10 pt risk
            take_profit=130.0,  # 30 pt target (3.0R)
            risk_usd=100.0
        )
        
        # Price rises to 118.0 (+1.8R MFE)
        self.engine.update_price("TEST/ETH", 118.0)
        
        # Price reverses to 89.0 (Initial SL breached)
        resolved = self.engine.update_price("TEST/ETH", 89.0)
        self.assertEqual(len(resolved), 1)
        res = resolved[0]
        self.assertAlmostEqual(res["live_ratchet_r"], 0.0)    # Stopped at Break-Even
        self.assertAlmostEqual(res["shadow_partial_r"], 0.75) # Kept +0.75R banked
        self.assertAlmostEqual(res["shadow_binary_r"], -1.0)  # Binary took full loss

    def test_pullback_after_2_8r_scenario(self):
        """When trade reaches +2.8R and reverses, live gets +1.0R (Lock), partial gets +1.25R, binary gets -1.0R."""
        self.engine.register_trade(
            symbol="TEST/SOL",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk_usd=100.0
        )
        
        # Price rises to 128.0 (+2.8R MFE)
        self.engine.update_price("TEST/SOL", 128.0)
        
        # Price reverses to 89.0 (Initial SL breached)
        resolved = self.engine.update_price("TEST/SOL", 89.0)
        self.assertEqual(len(resolved), 1)
        res = resolved[0]
        self.assertAlmostEqual(res["live_ratchet_r"], 1.0)     # Stopped at +1.0R lock
        self.assertAlmostEqual(res["shadow_partial_r"], 1.25)  # 0.75R banked + 0.50R locked = 1.25R
        self.assertAlmostEqual(res["shadow_binary_r"], -1.0)

    def test_direct_stop_loss_scenario(self):
        """When trade goes straight to stop loss, all models record -1.0R."""
        self.engine.register_trade(
            symbol="TEST/XAU",
            direction="SHORT",
            entry_price=100.0,
            stop_loss=110.0,
            take_profit=70.0,
            risk_usd=100.0
        )
        
        # Price rises directly to 111.0 (SL breached)
        resolved = self.engine.update_price("TEST/XAU", 111.0)
        self.assertEqual(len(resolved), 1)
        res = resolved[0]
        self.assertAlmostEqual(res["live_ratchet_r"], -1.0)
        self.assertAlmostEqual(res["shadow_partial_r"], -1.0)
        self.assertAlmostEqual(res["shadow_binary_r"], -1.0)

    def test_buffered_stop_deeper_plunge_scenario(self):
        """When price blows straight past the 1.25x buffered stop, buffered stop records -1.25R (larger drawdown)."""
        self.engine.register_trade(
            symbol="TEST/PLUNGE",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,    # risk_dist = 10, buffered_sl = 100 - 12.5 = 87.5
            take_profit=130.0,
            risk_usd=100.0
        )
        
        # Price plunges directly to 86.0 (below both initial SL and buffered SL)
        resolved = self.engine.update_price("TEST/PLUNGE", 86.0)
        self.assertEqual(len(resolved), 1)
        res = resolved[0]
        self.assertAlmostEqual(res["live_ratchet_r"], -1.0)
        self.assertAlmostEqual(res["shadow_buffered_r"], -1.25)
        self.assertAlmostEqual(res["shadow_buffered_pnl"], -125.0)

    def test_leaderboard_contains_shadow_buffered_and_session(self):
        """Verifies get_leaderboard outputs shadow_buffered and shadow_session_adaptive keys."""
        board = self.engine.get_leaderboard()
        self.assertIn("shadow_buffered", board)
        self.assertIn("shadow_session_adaptive", board)

if __name__ == '__main__':
    unittest.main()
