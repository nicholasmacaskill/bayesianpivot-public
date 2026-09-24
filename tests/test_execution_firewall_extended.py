"""
Extended Unit Tests for 9-Gate Sovereign Execution Firewall
===========================================================
"""

import os
import unittest
import tempfile
from unittest.mock import patch

from src.core.execution_firewall import ExecutionFirewall, COOLDOWNS_FILE_PATH
from src.core.config import Config


class TestExecutionFirewallExtended(unittest.TestCase):

    def test_invariant_1_mandatory_stop_loss(self):
        # Stop loss None or 0 must fail
        approved, reason = ExecutionFirewall.audit_trade_request(
            symbol="BTC/USD",
            side="buy",
            stop_loss=None,
            take_profit=79000.0,
            ai_score=9.0
        )
        self.assertFalse(approved)
        self.assertTrue("Gate 1" in reason or "Stop Loss is mandatory" in reason)

    def test_invariant_4_ai_conviction_threshold(self):
        # AI Score < 8.0 must fail
        approved, reason = ExecutionFirewall.audit_trade_request(
            symbol="BTC/USD",
            side="buy",
            stop_loss=76000.0,
            take_profit=79000.0,
            ai_score=7.5,
            bypass_killzone=True,
            bypass_weekend=True
        )
        self.assertFalse(approved)
        self.assertTrue("Gate 4" in reason or "8.0/10.0 threshold" in reason)

    def test_invariant_6_fleet_anti_stacking_zero_collision(self):
        # Existing position on ETH/USD must reject new ETH/USD order
        open_positions = [
            {"id": "pos_123", "symbol": "ETHUSD", "side": "BUY", "qty": 4.62, "price": 2470.0}
        ]
        
        approved, reason = ExecutionFirewall.audit_trade_request(
            symbol="ETH/USD",
            side="buy",
            stop_loss=2450.0,
            take_profit=2500.0,
            ai_score=9.0,
            bypass_killzone=True,
            open_positions=open_positions,
            bypass_cooldown=True,
            bypass_weekend=True
        )
        self.assertFalse(approved)
        self.assertTrue("Gate 6" in reason or "Anti-Stacking" in reason)
        self.assertIn("Active position already exists on ETH/USD", reason)

    def test_invariant_7_fleet_max_concurrent_positions(self):
        # Fleet already has 2 distinct symbols (e.g. BTC and ETH) -> Reject new XAU order
        open_positions = [
            {"id": "pos_1", "symbol": "BTCUSD", "side": "BUY"},
            {"id": "pos_2", "symbol": "ETHUSD", "side": "BUY"}
        ]
        
        with patch.object(Config, 'MAX_CONCURRENT_FLEET_POSITIONS', 2):
            approved, reason = ExecutionFirewall.audit_trade_request(
                symbol="XAU/USD",
                side="buy",
                stop_loss=2400.0,
                take_profit=2450.0,
                ai_score=9.0,
                bypass_killzone=True,
                open_positions=open_positions,
                bypass_cooldown=True,
                bypass_weekend=True
            )
            self.assertFalse(approved)
            self.assertTrue("Gate 7" in reason or "Max Concurrent Fleet Positions" in reason)

    def test_invariant_8_persistent_symbol_cooldown(self):
        # Record a trade execution 5 minutes ago -> Must be rejected by 30-minute cooldown
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_cooldown_file = os.path.join(tmp_dir, "test_cooldowns.json")
            with patch("src.core.execution_firewall.COOLDOWNS_FILE_PATH", test_cooldown_file):
                # Record execution
                ExecutionFirewall.record_trade_execution("ETH/USD")
                
                # Audit trade request without bypass
                approved, reason = ExecutionFirewall.audit_trade_request(
                    symbol="ETH/USD",
                    side="buy",
                    stop_loss=2450.0,
                    take_profit=2500.0,
                    ai_score=9.0,
                    bypass_killzone=True,
                    open_positions=[],
                    bypass_cooldown=False,
                    bypass_weekend=True
                )
                self.assertFalse(approved)
                self.assertTrue("Gate 8" in reason or "Debounce Cooldown" in reason)

    def test_invariant_9_account_eligibility(self):
        # Test LIQUIDATION_ONLY account
        eligible, reason = ExecutionFirewall.is_account_eligible(
            email="test@upcomers.com",
            status="LIQUIDATION_ONLY",
            equity=9600.0,
            hard_floor=9500.0
        )
        self.assertFalse(eligible)
        self.assertIn("LIQUIDATION_ONLY", reason)

        # Test account exceeding max open positions (>= 2)
        eligible_pos_cap, reason_pos_cap = ExecutionFirewall.is_account_eligible(
            email="busy@upcomers.com",
            status="ACTIVE",
            equity=25500.0,
            hard_floor=23750.0,
            open_positions_count=2
        )
        self.assertFalse(eligible_pos_cap)
        self.assertIn("already has 2 open positions", reason_pos_cap)

        # Test fully eligible account (0 open positions, any buffer — we trade until prop firm stops us)
        eligible_ok, reason_ok = ExecutionFirewall.is_account_eligible(
            email="healthy@upcomers.com",
            status="ACTIVE",
            equity=25500.0,
            hard_floor=23750.0,
            open_positions_count=0
        )
        self.assertTrue(eligible_ok)
        self.assertEqual(reason_ok, "ELIGIBLE")


    def test_invariant_9_consistency_profit_ceiling(self):
        """Verify is_account_eligible blocks accounts that reached the 20% consistency daily profit cap."""
        # Account 1 reached $380 daily cap ($400 is 20% of $2,000 target)
        eligible_acc1, reason_acc1 = ExecutionFirewall.is_account_eligible(
            email="s79qv3xetj@upcomers.com",
            status="ACTIVE",
            equity=25286.0,
            hard_floor=25000.0,
            open_positions_count=0,
            today_realized_profit=385.0
        )
        self.assertFalse(eligible_acc1)
        self.assertIn("20% Consistency Daily Profit Ceiling", reason_acc1)

        # 50k Account reached $760 daily cap ($800 is 20% of $4,000 target)
        eligible_50k, reason_50k = ExecutionFirewall.is_account_eligible(
            email="jfcuue7er3@upcomers.com",
            status="ACTIVE",
            equity=50800.0,
            hard_floor=47500.0,
            open_positions_count=0,
            today_realized_profit=770.0
        )
        self.assertFalse(eligible_50k)
        self.assertIn("20% Consistency Daily Profit Ceiling", reason_50k)

        # 50k Account below daily cap ($250 profit) is approved
        eligible_ok, reason_ok = ExecutionFirewall.is_account_eligible(
            email="jfcuue7er3@upcomers.com",
            status="ACTIVE",
            equity=50250.0,
            hard_floor=47500.0,
            open_positions_count=0,
            today_realized_profit=250.0
        )
        self.assertTrue(eligible_ok)
        self.assertEqual(reason_ok, "ELIGIBLE")

    def test_invariant_2_friday_pre_weekend_entry_lockout(self):
        """Verify audit_trade_request rejects new entries on Friday after 18:00 UTC."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        # Friday 18:30 UTC
        friday_late = datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)
        with patch("src.core.execution_firewall.datetime") as mock_dt:
            mock_dt.now.return_value = friday_late
            approved, reason = ExecutionFirewall.audit_trade_request(
                symbol="BTC/USD",
                side="buy",
                stop_loss=60000.0,
                take_profit=65000.0,
                ai_score=9.0,
                bypass_killzone=True,
                bypass_circuit_breaker=True
            )
            self.assertFalse(approved)
            self.assertIn("Friday Pre-Weekend Lockout active", reason)


if __name__ == "__main__":
    unittest.main()
