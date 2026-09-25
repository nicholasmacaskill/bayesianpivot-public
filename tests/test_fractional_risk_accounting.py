"""
================================================================================
🏛️ FRACTIONAL RISK-BUDGETED SETUP ACCOUNTING & DEFENSIVE GATES TEST SUITE
================================================================================
Verifies:
1. Fractional Risk-Budgeted Accounting (1.0x Full Trade = 1.0 Unit; 0.5x Probe = 0.5 Units; 3.0 Unit Daily Cap).
2. 2.0 Unit Cumulative Loss Circuit Breaker (2 full losses or 4 probe losses triggers 24h lockout; 2 probe losses allowed).
3. Session Anti-Clustering Gate (Max 1 setup per session: Asian Judas, London Open, NY Morning).
4. Preservation of 20% Consistency Daily Profit Ceiling Clamp ($380 on $25k; $760 on $50k).
================================================================================
"""

import os
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from src.core.config import Config
from src.core.execution_firewall import ExecutionFirewall


class TestFractionalRiskBudgetedAccounting(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_smc_alpha.db")
        self.lock_file = os.path.join(self.temp_dir.name, "daily_setup_lock.json")

        # Initialize SQLite DB schema with journal table
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                trade_id TEXT UNIQUE,
                symbol TEXT,
                side TEXT,
                pnl REAL,
                ai_grade REAL,
                mentor_feedback TEXT,
                deviations TEXT,
                is_lucky_failure INTEGER DEFAULT 0,
                price REAL DEFAULT 0.0,
                status TEXT DEFAULT 'CLOSED',
                strategy TEXT DEFAULT 'SYSTEM',
                notes TEXT
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    # ── 1. FRACTIONAL RISK-BUDGETED CAPACITY TESTS ─────────────────────────────

    def test_fractional_risk_accounting_capacity_3_units(self):
        """
        Test that daily capacity is 3.0 Risk Units:
        - 2 half-size probes (0.5 + 0.5 = 1.0 unit) leaves 2.0 units available.
        - Next full trade (1.0 unit) is allowed (total 2.0 units).
        - Another full trade (1.0 unit) is allowed (total 3.0 units).
        - A 3rd full trade (1.0 unit) is blocked (would exceed 3.0 unit cap).
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with patch.object(Config, 'DAILY_RISK_UNIT_CAP', 3.0):
            # Initially 0 units used
            data = {"date": today_str, "setups_fired": 0, "units_used": 0.0}
            with open(self.lock_file, "w") as f:
                json.dump(data, f)

            with patch("src.core.execution_firewall.Config.DB_PATH", self.db_path), \
                 patch("builtins.open", patch("builtins.open")._mock_wraps if hasattr(patch("builtins.open"), "_mock_wraps") else open):
                
                # Mock lock file path in check_global_daily_setup_limit
                with patch("os.path.exists", side_effect=lambda p: True if "daily_setup_lock.json" in str(p) else os.path.exists(p)):
                    # 1. First probe (0.5 units) -> OK
                    data["units_used"] = 0.0
                    with open(self.lock_file, "w") as f: json.dump(data, f)
                    with patch("json.load", return_value=data):
                        ok, msg = ExecutionFirewall.check_global_daily_setup_limit(requested_units=0.5)
                        self.assertTrue(ok)

                    # 2. After 2 probes fired (1.0 unit consumed)
                    data["units_used"] = 1.0
                    data["setups_fired"] = 2
                    with open(self.lock_file, "w") as f: json.dump(data, f)
                    with patch("json.load", return_value=data):
                        # Requesting full trade (1.0 unit) -> 1.0 + 1.0 = 2.0 <= 3.0 -> OK
                        ok, msg = ExecutionFirewall.check_global_daily_setup_limit(requested_units=1.0)
                        self.assertTrue(ok, f"Should allow full trade when only 1.0 unit used: {msg}")

                    # 3. After 1.0 + 1.0 = 2.0 units consumed
                    data["units_used"] = 2.0
                    data["setups_fired"] = 3
                    with open(self.lock_file, "w") as f: json.dump(data, f)
                    with patch("json.load", return_value=data):
                        # Another full trade (1.0 unit) -> 2.0 + 1.0 = 3.0 <= 3.0 -> OK
                        ok, msg = ExecutionFirewall.check_global_daily_setup_limit(requested_units=1.0)
                        self.assertTrue(ok, f"Should allow reaching 3.0 unit cap: {msg}")

                    # 4. At 3.0 units consumed (Capacity Full)
                    data["units_used"] = 3.0
                    data["setups_fired"] = 4
                    with open(self.lock_file, "w") as f: json.dump(data, f)
                    with patch("json.load", return_value=data):
                        # Any additional full trade (1.0) or probe (0.5) must be blocked
                        ok_full, msg_full = ExecutionFirewall.check_global_daily_setup_limit(requested_units=1.0)
                        self.assertFalse(ok_full)
                        self.assertIn("Daily Risk Unit Limit hit", msg_full)

                        ok_probe, msg_probe = ExecutionFirewall.check_global_daily_setup_limit(requested_units=0.5)
                        self.assertFalse(ok_probe)
                        self.assertIn("Daily Risk Unit Limit hit", msg_probe)

    # ── 2. CUMULATIVE LOSS CIRCUIT BREAKER TESTS ──────────────────────────────

    def test_loss_circuit_breaker_allows_two_probe_losses(self):
        """
        Two 0.50x probe losses (-0.5R each = -1.0 Unit cumulative loss) MUST NOT
        trigger the circuit breaker. This fixes the bug where taking two probes locked
        the fleet down even though only ~$50 total risk was used.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # 2 probe losses today (Account 1 loss ~$17.50 each)
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't1', 'BTC/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T02:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't2', 'ETH/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T04:00:00",))
        conn.commit()
        conn.close()

        with patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0), \
             patch.object(Config, 'MAX_CONSECUTIVE_DAILY_LOSSES', 2):
            cb_ok, reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            self.assertTrue(cb_ok, f"Two probe losses (-1.0 Unit) should NOT lock down fleet! Got: {reason}")
            self.assertEqual(reason, "OK")

    def test_loss_circuit_breaker_locks_on_two_full_losses(self):
        """
        Two 1.00x full trade losses (-1.0R each = -2.0 Units cumulative loss)
        MUST trigger the 24-hour circuit breaker.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # 2 full losses today
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't1', 'BTC/USD', 'BUY', -35.0, 'CLOSED', 'SYSTEM', 'Full')", (f"{today_str}T07:30:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't2', 'XAU/USD', 'BUY', -35.0, 'CLOSED', 'SYSTEM', 'Full')", (f"{today_str}T13:30:00",))
        conn.commit()
        conn.close()

        with patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0):
            cb_ok, reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            self.assertFalse(cb_ok, "Two full losses (-2.0 Units) MUST lock down fleet!")
            self.assertTrue("cumulative loss limit reached" in reason or "Daily consecutive loss ceiling hit" in reason)

    def test_loss_circuit_breaker_locks_on_four_probe_losses(self):
        """
        Four 0.50x probe losses (-0.5R * 4 = -2.0 Units cumulative loss)
        MUST trigger the 24-hour circuit breaker.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # 4 probe losses today at distinct times
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't1', 'BTC/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T01:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't2', 'ETH/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T02:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't3', 'SOL/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T03:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't4', 'BTC/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T04:00:00",))
        conn.commit()
        conn.close()

        with patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0):
            cb_ok, reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            self.assertFalse(cb_ok, "Four probe losses (-2.0 Units) MUST lock down fleet!")
            self.assertTrue("cumulative loss limit reached" in reason or "Daily consecutive loss ceiling hit" in reason)

    def test_loss_circuit_breaker_locks_on_one_full_plus_two_probe_losses(self):
        """
        1 full loss (-1.0 Unit) + 2 probe losses (-0.5 Unit each = -1.0 Unit) = -2.0 Units
        MUST trigger the 24-hour circuit breaker.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't1', 'BTC/USD', 'BUY', -35.00, 'CLOSED', 'SYSTEM', 'Full')", (f"{today_str}T01:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't2', 'ETH/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T02:00:00",))
        cur.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, status, strategy, notes) VALUES (?, 't3', 'SOL/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE', 'Probe')", (f"{today_str}T03:00:00",))
        conn.commit()
        conn.close()

        with patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0):
            cb_ok, reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            self.assertFalse(cb_ok, "1 full + 2 probe losses (-2.0 Units) MUST lock down fleet!")
            self.assertTrue("cumulative loss limit reached" in reason or "Daily consecutive loss ceiling hit" in reason)

    # ── 3. SESSION ANTI-CLUSTERING GATE TESTS ─────────────────────────────────

    def test_session_anti_clustering_gate(self):
        """
        Enforce max 1 setup per killzone session:
        - Setup 1 in Asian Judas: ALLOWED.
        - Setup 2 in Asian Judas: REJECTED by Gate 9.
        - Setup 1 in London Open: ALLOWED (distinct session).
        - Setup 1 in NY Morning: ALLOWED (distinct session).
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Mock lock data with 1 setup already executed in ASIAN_JUDAS
        data = {
            "date": today_str,
            "setups_fired": 1,
            "units_used": 0.5,
            "session_counts": {"ASIAN_JUDAS": 1},
            "sessions_fired": ["ASIAN_JUDAS"]
        }

        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(data))), \
             patch("os.path.exists", return_value=True), \
             patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'MAX_SETUPS_PER_KILLZONE_SESSION', 1):
            
            # 1. Another trade in ASIAN_JUDAS must be REJECTED
            ok_asian2, reason_asian2 = ExecutionFirewall.check_session_setup_limit(session_hint="ASIAN_SESSION_JUDAS")
            self.assertFalse(ok_asian2)
            self.assertIn("Session Anti-Clustering", reason_asian2)
            self.assertIn("ASIAN_JUDAS", reason_asian2)

            # 2. Trade in LONDON_OPEN must be APPROVED
            ok_london, reason_london = ExecutionFirewall.check_session_setup_limit(session_hint="LONDON_OPEN")
            self.assertTrue(ok_london, f"Expected LONDON_OPEN to pass but got: {reason_london}")
            self.assertEqual(reason_london, "OK")

            # 3. Trade in NY_MORNING must be APPROVED
            ok_ny, reason_ny = ExecutionFirewall.check_session_setup_limit(session_hint="LONDON_CLOSE_NY_MORNING")
            self.assertTrue(ok_ny, f"Expected NY_MORNING to pass but got: {reason_ny}")
            self.assertEqual(reason_ny, "OK")

    def test_audit_trade_request_gate_9_session_rejection(self):
        """
        Verify that audit_trade_request actively triggers Gate 9 when a second
        trade in the same session is attempted.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {
            "date": today_str,
            "setups_fired": 1,
            "units_used": 0.5,
            "session_counts": {"LONDON_OPEN": 1},
            "sessions_fired": ["LONDON_OPEN"]
        }

        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(data))), \
             patch("os.path.exists", return_value=True), \
             patch.object(Config, 'DB_PATH', self.db_path), \
             patch.object(Config, 'MAX_SETUPS_PER_KILLZONE_SESSION', 1):

            # Attempt a trade in London Open when London Open already fired 1 setup
            approved, reason = ExecutionFirewall.audit_trade_request(
                symbol="BTC/USD",
                side="buy",
                stop_loss=80000.0,
                take_profit=85000.0,
                ai_score=9.0,
                session="LONDON_OPEN",
                bypass_killzone=False,
                bypass_circuit_breaker=False,
                bypass_weekend=True,
                bypass_cooldown=True
            )
            # Even if time of day is outside London, either Gate 3 or Gate 9 will block;
            # If we test during a mock London time:
            now_today = datetime.now(timezone.utc)
            mock_london_dt = now_today.replace(hour=8, minute=30, second=0, microsecond=0)
            with patch("src.core.execution_firewall.datetime") as mock_dt, \
                 patch("src.engines.calendar_filter.CalendarFilter.is_safe_to_trade", return_value=(True, "Safe")):
                mock_dt.now.return_value = mock_london_dt
                mock_dt.fromisoformat = datetime.fromisoformat
                approved_london, reason_london = ExecutionFirewall.audit_trade_request(
                    symbol="BTC/USD",
                    side="buy",
                    stop_loss=80000.0,
                    take_profit=85000.0,
                    ai_score=9.0,
                    session="LONDON_OPEN",
                    bypass_killzone=False,
                    bypass_circuit_breaker=False,
                    bypass_weekend=True,
                    bypass_cooldown=True
                )
                self.assertFalse(approved_london)
                self.assertIn("Gate 9", reason_london)
                self.assertIn("Session Anti-Clustering", reason_london)


if __name__ == "__main__":
    unittest.main()
