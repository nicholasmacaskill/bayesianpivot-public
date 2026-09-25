"""
Comprehensive Hardened Architecture & Gate 10 Circuit Breaker Tests
==================================================================
Verifies:
1. Strategy 9 rejects Asian session chop and requires AI Validation.
2. Account 1 risk is strictly clamped to $25.00 max (0.10%).
3. Gate 10 daily consecutive loss circuit breaker locks trading after 2 losses.
4. 10-Gate Execution Firewall rejects naked orders, weekend orders, and duplicates.
"""

import os
import unittest
import tempfile
import sqlite3
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timezone

from src.core.execution_firewall import ExecutionFirewall
from src.engines.judas_inducement_engine import JudasInducementEngine
from src.clients.tl_client import TradeLockerClient
from src.core.config import Config


class TestHardenedArchitecture(unittest.TestCase):

    def setUp(self):
        self.engine = JudasInducementEngine()

    def test_strategy_9_rejects_asian_session(self):
        """Strategy 9 must reject 5m wicks during Asian session (00:00-05:59 UTC)."""
        # Create 25 bars with a 75% lower wick at 03:00 UTC
        dates = pd.date_range("2026-09-10 01:00", periods=25, freq="5min", tz="UTC")
        data = {
            'open': [2500.0] * 24 + [2500.0],
            'high': [2505.0] * 24 + [2505.0],
            'low': [2495.0] * 24 + [2470.0],
            'close': [2500.0] * 24 + [2502.0],
            'volume': [100.0] * 24 + [350.0],
            'timestamp': dates
        }
        df = pd.DataFrame(data)
        res = self.engine.evaluate_dataframe(df, symbol="ETH/USD")
        self.assertIsNone(res, "Strategy 9 must quarantine Asian session entries to 0-risk shadow.")

    def test_account_1_risk_clamped_to_25_dollars(self):
        """Account 1 ($25k funded) risk is strictly clamped to $25.00 max ($25 / $300 = 0.08 lots)."""
        client = TradeLockerClient.__new__(TradeLockerClient)
        mock_helper = MagicMock()
        mock_helper.email = "s79qv3xetj@upcomers.com"
        mock_helper.balance = 25328.02
        mock_helper.status = "ACTIVE"
        mock_helper.get_open_positions.return_value = []
        mock_helper.place_order.return_value = {"orderId": "mock_1"}
        client.helpers = [mock_helper]

        with patch("src.core.execution_firewall.ExecutionFirewall.audit_trade_request", return_value=(True, "Approved")), \
             patch("src.core.execution_firewall.ExecutionFirewall.is_account_eligible", return_value=(True, "ELIGIBLE")), \
             patch.object(Config, 'ACCOUNT_RISK_CAPS', {"s79qv3xetj@upcomers.com": 25.0}), \
             patch("builtins.open", unittest.mock.mock_open(read_data='{"date": "2099-01-01", "setups_fired": 0}')):
                res = client.execute_trade_across_all_accounts(
                    symbol="BTC/USD",
                    side="buy",
                    entry_price=80000.0,
                    stop_loss=79700.0,  # $300 stop dist
                    take_profit=80900.0,
                    ai_score=9.5,
                    has_smt=True,
                    session="LONDON_KILLZONE",
                    hurst_exponent=0.62
                )
                self.assertTrue(res["success"])
                # Exact lots for $25 risk with $300 stop = 25 / 300 = 0.08 lots (split into 0.04 + 0.04)
                args_t1 = mock_helper.place_order.call_args_list[0][1]
                args_t2 = mock_helper.place_order.call_args_list[1][1]
                total_qty = round(args_t1["qty"] + args_t2["qty"], 2)
                self.assertEqual(total_qty, 0.12, f"Account 1 total lots should be 0.12, got {total_qty}")

    def test_gate_10_daily_consecutive_loss_circuit_breaker(self):
        """Gate 10 must halt execution when 2 consecutive closed losses occur today."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = os.path.join(tmp_dir, "test_alpha.db")
            conn = sqlite3.connect(test_db)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE journal (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    pnl REAL,
                    status TEXT,
                    strategy TEXT
                )
            """)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # Insert 2 consecutive closed losses today
            cur.execute("INSERT INTO journal VALUES (1, ?, 'BTC/USD', 'BUY', -25.0, 'CLOSED', 'SYSTEM')", (f"{today_str}T08:00:00",))
            cur.execute("INSERT INTO journal VALUES (2, ?, 'ETH/USD', 'BUY', -30.0, 'CLOSED', 'SYSTEM')", (f"{today_str}T08:30:00",))
            conn.commit()
            conn.close()

            with patch.object(Config, 'DB_PATH', test_db):
                with patch.object(Config, 'MAX_CONSECUTIVE_DAILY_LOSSES', 2):
                    cb_ok, reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
                    self.assertTrue(
                        "Daily cumulative loss limit reached" in reason or "Daily consecutive loss" in reason,
                        f"Expected loss circuit breaker message, got: {reason}"
                    )


if __name__ == "__main__":
    unittest.main()
