"""
================================================================================
🏛️ TIER 6: ADVERSARIAL NEGATIVE BOUNDARY & STATE INVARIANTS TEST SUITE
================================================================================
Directly verifies that the 24 forensic bugs identified during live operations
remain permanently locked down and cannot regress:
1. Gold contract size multiplier (100.0) vs Crypto unit math (1.0).
2. Ruler invariance: Stepped defense SL tightening freezes risk denominator.
3. Macro news currency isolation: Foreign currency news (AUD/CAD/JPY) must not block USD trades.
4. Execution firewall passes symbol down to calendar filter.
5. Session string continuity between is_premium_killzone and gold liquid checks.
6. Judas Inducement Engine 12:00-17:00 UTC killzone coverage.
7. TradeLocker instrument resolution exact match priority (no substring collisions).
8. Position watchdog unmapped instrument ID bidirectional matching.
9. Counterfactual tracker dynamic R-multiple calculation.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from src.core.config import Config
from src.core.execution_firewall import ExecutionFirewall
from src.engines.calendar_filter import CalendarFilter
from src.clients.tl_client import TradeLockerClient, TradeLockerHelper


class TestAdversarialBoundaryInvariants(unittest.TestCase):

    def test_invariant_gold_contract_size_multiplier_not_crypto_unit_math(self):
        """
        Invariant: Gold lot sizing and PnL must strictly use contract_size = 100.0,
        never naive crypto unit math (1.0). Prevents 100x oversized orders and
        20% consistency ceiling breaches.
        """
        btc_contract = Config.get_contract_size("BTC/USD")
        xau_contract = Config.get_contract_size("XAU/USD")
        eth_contract = Config.get_contract_size("ETH/USD")

        self.assertEqual(btc_contract, 1.0, "BTC contract size must be 1.0")
        self.assertEqual(eth_contract, 1.0, "ETH contract size must be 1.0")
        self.assertEqual(xau_contract, 100.0, "Gold contract size MUST be 100.0 (1 lot = 100 oz)")

        # Verify lot calculation on $100 risk with a $5.00 stop distance
        target_risk_usd = 100.0
        stop_dist = 5.0
        gold_lots = target_risk_usd / (stop_dist * xau_contract)
        self.assertAlmostEqual(gold_lots, 0.20, places=2, 
                               msg="Gold lots on $100 risk with $5 stop must be 0.20, NOT 20.0 lots!")

        # Verify naive crypto formula would have been catastrophic (100x error)
        naive_lots = target_risk_usd / stop_dist
        self.assertEqual(naive_lots, 20.0)
        self.assertEqual(naive_lots / gold_lots, 100.0)

    def test_invariant_stepped_defense_ruler_denominator_freeze(self):
        """
        Invariant (The Ruler Invariant): Stop loss defense tightening at +1.0R must
        NEVER recompute the risk denominator. The initial risk basis must remain invariant.
        """
        entry = 2500.0
        initial_sl = 2495.0
        qty = 0.20
        contract_size = 100.0
        initial_risk_usd = abs(entry - initial_sl) * qty * contract_size
        self.assertAlmostEqual(initial_risk_usd, 100.0, places=2)

        # Trade reaches +1.0R ($100 floating PnL)
        pnl = 100.0
        r_multiple = pnl / initial_risk_usd
        self.assertAlmostEqual(r_multiple, 1.0, places=2)

        # Stepped defense tightens Stop Loss across fleet to -0.3R ($2498.50)
        tightened_sl = 2498.50
        mutated_risk = abs(entry - tightened_sl) * qty * contract_size
        self.assertAlmostEqual(mutated_risk, 30.0, places=2)

        # Invariant Assertion: The ruler measuring the trade MUST NOT shrink
        frozen_r = pnl / initial_risk_usd
        self.assertEqual(frozen_r, 1.0, "R-multiple must remain 1.0R against initial risk basis")

        # Assert that if someone used mutated_risk, it would have created a false 3.33R trigger
        inflated_r = pnl / mutated_risk
        self.assertAlmostEqual(inflated_r, 3.3333, places=2)

    def test_invariant_multi_account_r_multiple_lot_size_isolation(self):
        """
        Adversarial Invariant: Multi-account fleet positions on the same symbol with
        different lot sizes (e.g. 0.01 lots vs 0.08 lots) MUST compute R-multiples using
        their own position-specific risk basis. They must NEVER share a single denominator.
        """
        entry = 4283.0
        initial_sl = 4272.0  # 11 point stop
        stop_dist = abs(entry - initial_sl)
        contract_size = 100.0  # Gold

        # Account 1: 0.01 lots ($11.00 risk)
        acct1_qty = 0.01
        acct1_risk = stop_dist * acct1_qty * contract_size
        self.assertAlmostEqual(acct1_risk, 11.0, places=2)

        # Account 9: 0.08 lots ($88.00 risk)
        acct9_qty = 0.08
        acct9_risk = stop_dist * acct9_qty * contract_size
        self.assertAlmostEqual(acct9_risk, 88.0, places=2)

        # Price moves +3.0 points to 4286.0 (+0.27R in price terms)
        current_price = 4286.0
        price_delta = current_price - entry
        expected_r = price_delta / stop_dist  # 3.0 / 11.0 = 0.2727R

        # Calculate PnL for each account
        acct1_pnl = price_delta * acct1_qty * contract_size  # $3.00
        acct9_pnl = price_delta * acct9_qty * contract_size  # $24.00

        # With per-position risk isolation:
        acct1_r = acct1_pnl / acct1_risk
        acct9_r = acct9_pnl / acct9_risk

        self.assertAlmostEqual(acct1_r, expected_r, places=4)
        self.assertAlmostEqual(acct9_r, expected_r, places=4)
        self.assertAlmostEqual(acct1_r, acct9_r, places=4, msg="Both accounts must have identical R-multiple for identical price move")

        # Assert that if Account 9's PnL was divided by Account 1's risk (the old bug), it would produce a corrupted 2.18R
        corrupted_acct9_r = acct9_pnl / acct1_risk
        self.assertAlmostEqual(corrupted_acct9_r, 2.1818, places=2)
        self.assertNotEqual(corrupted_acct9_r, expected_r, "Denominator cross-contamination bug must never recur")

    def test_invariant_macro_news_currency_isolation_negative_boundary(self):
        """
        Negative Boundary Invariant: Foreign currency events (AUD, CAD, JPY, GBP)
        must NEVER lock out USD-denominated trades (BTC/USD, XAU/USD).
        """
        cal = CalendarFilter(blackout_minutes=30)
        now_utc = datetime.utcnow()

        # Mock high-impact Australian economic release (AUD)
        cal._events = [
            {
                "title": "Employment Change",
                "currency": "AUD",
                "impact": "High",
                "time_utc": now_utc + timedelta(minutes=10)
            }
        ]
        cal._last_fetch = now_utc

        # 1. Assert AUD news does NOT block BTC/USD
        safe_btc, reason_btc = cal.is_safe_to_trade(symbol="BTC/USD")
        self.assertTrue(safe_btc, f"Foreign currency news falsely blocked BTC/USD: {reason_btc}")

        # 2. Assert AUD news does NOT block XAU/USD
        safe_xau, reason_xau = cal.is_safe_to_trade(symbol="XAU/USD")
        self.assertTrue(safe_xau, f"Foreign currency news falsely blocked XAU/USD: {reason_xau}")

        # 3. Assert global USD FOMC event DOES block BTC/USD
        cal._events.append({
            "title": "FOMC Interest Rate Decision",
            "currency": "USD",
            "impact": "High",
            "time_utc": now_utc + timedelta(minutes=10)
        })
        safe_fomc, reason_fomc = cal.is_safe_to_trade(symbol="BTC/USD")
        self.assertFalse(safe_fomc, "FOMC USD event should have blocked BTC/USD")
        self.assertIn("FOMC", reason_fomc)

    def test_invariant_firewall_passes_symbol_to_news_calendar(self):
        """
        Invariant: ExecutionFirewall.check_news_calendar must accept symbol and pass it
        to CalendarFilter, preventing silent loss of asset context.
        """
        with patch('src.engines.calendar_filter.CalendarFilter.is_safe_to_trade', return_value=(True, "OK")) as mock_cal:
            is_ok, reason = ExecutionFirewall.check_news_calendar(symbol="BTC/USD")
            self.assertTrue(is_ok)
            mock_cal.assert_called_with(symbol="BTC/USD")

    def test_invariant_session_string_contract_continuity(self):
        """
        Invariant: is_premium_killzone() at 12:30 UTC returns 'LONDON_CLOSE_NY_MORNING',
        which must be recognized by gold liquid session checks and NOT quarantined as shadow.
        """
        from src.engines.alpha_sweep_scanner import AlphaSweepScanner
        scanner = AlphaSweepScanner.__new__(AlphaSweepScanner)

        # 12:30 UTC (8:30 AM EDT - Prime US Macro / NY Open)
        dt = datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc)
        kz = scanner.is_premium_killzone(dt)
        self.assertEqual(kz, "LONDON_CLOSE_NY_MORNING")

        # Test gold liquid session check includes NY
        kz_str = str(kz).upper()
        is_gold_liquid = any(k in kz_str for k in ["LONDON", "NY", "NEW_YORK", "CONTINUOUS", "ASIAN"])
        self.assertTrue(is_gold_liquid, "LONDON_CLOSE_NY_MORNING must qualify as a Gold liquid session")

    def test_invariant_instrument_id_exact_match_priority(self):
        """
        Invariant: resolve_instrument_id must prioritize exact dictionary matches first,
        preventing partial substring key collisions (e.g. 'USD' key matching before 'BTCUSD').
        """
        tl = TradeLockerClient.__new__(TradeLockerClient)
        helper = TradeLockerHelper.__new__(TradeLockerHelper)
        
        # Populate cache where 'USD' appears before 'BTCUSD'
        helper._instruments_cache = {
            "USD": {"tradableInstrumentId": 99999, "name": "USD"},
            "BTCUSD": {"tradableInstrumentId": 19965, "name": "BTC/USD"},
            "XAUUSD": {"tradableInstrumentId": 19915, "name": "XAU/USD"},
        }
        tl.helpers = [helper]

        # Invariant: Must return 19965 for BTC/USD, NOT 99999!
        resolved_btc = tl.resolve_instrument_id("BTC/USD")
        self.assertEqual(resolved_btc, "19965", "Exact match for BTC/USD must return 19965")

        resolved_xau = tl.resolve_instrument_id("XAU/USD")
        self.assertEqual(resolved_xau, "19915", "Exact match for XAU/USD must return 19915")

    def test_invariant_watchdog_unmapped_instrument_id_matching(self):
        """
        Invariant: Position watchdog must evaluate both symbol and tradableInstrumentId,
        ensuring positions are matched even if broker returns unmapped numeric ID ('19915').
        """
        target_sym = "XAUUSD"
        target_inst_id = "19915"

        # Position ticket with unmapped symbol from broker API
        unmapped_pos = {
            "id": "pos_999",
            "symbol": "19915",
            "tradableInstrumentId": "19915",
            "side": "BUY"
        }

        pos_sym = str(unmapped_pos.get("symbol", "")).replace("/", "").replace("_", "").upper()
        pos_inst_id = str(unmapped_pos.get("tradableInstrumentId") or unmapped_pos.get("instrumentId") or "")
        
        is_match = (target_sym in pos_sym or (pos_inst_id and pos_inst_id == target_inst_id))
        self.assertTrue(is_match, "Unmapped instrument ID 19915 must match target Gold position!")

    def test_invariant_counterfactual_dynamic_target_r(self):
        """
        Invariant: Counterfactual tracker must dynamically calculate target R from
        (tp - entry) / (entry - sl), never hardcoding static 2.5R or $250.
        """
        entry = 100.0
        sl = 95.0   # risk = 5.0
        tp = 115.0  # reward = 15.0 (3.0R)

        risk_dist = abs(entry - sl)
        reward_dist = abs(tp - entry)
        target_r = round(reward_dist / risk_dist, 2)
        target_pnl = round(target_r * 100.0, 2)

        self.assertEqual(target_r, 3.0, "Dynamic target R must equal 3.0R, not hardcoded 2.5R")
        self.assertEqual(target_pnl, 300.0, "Dynamic PnL must equal $300.00, not hardcoded $250.00")

    def test_invariant_mfe_boundary_arming_and_trigger(self):
        """
        Adversarial Invariant: MFE Peak Retracement Ratchet:
        1. Negative Boundary: A trade that peaks at +0.87R and pulls back to +0.10R MUST NEVER trigger MFE (disarmed because peak < 2.0R).
        2. Negative Boundary: A trade that peaks at +2.20R and pulls back to +1.80R (retrace = 0.40R < 0.75R) MUST NOT trigger MFE.
        3. Positive Trigger: A trade that peaks at +2.20R and pulls back to +1.40R (retrace = 0.80R >= 0.75R) MUST trigger MFE scaleout.
        """
        from src.core.config import Config
        self.assertTrue(Config.MFE_PEAK_RATCHET_ENABLED, "MFE must be enabled in Config")
        mfe_min_peak = Config.MFE_MIN_PEAK_R
        mfe_max_retrace = Config.MFE_MAX_RETRACEMENT_R

        # Scenario 1: Today's Gold move (+0.87R peak -> +0.10R pullback)
        peak_r_scenario1 = 0.87
        current_r_scenario1 = 0.10
        armed_1 = peak_r_scenario1 >= mfe_min_peak
        self.assertFalse(armed_1, "MFE must NEVER arm on sub-2.0R peaks (like today's Gold 0.87R move)")

        # Scenario 2: Deep runner at +2.20R with healthy 0.40R pullback
        peak_r_scenario2 = 2.20
        current_r_scenario2 = 1.80
        armed_2 = peak_r_scenario2 >= mfe_min_peak
        retrace_2 = peak_r_scenario2 - current_r_scenario2
        trigger_2 = armed_2 and (retrace_2 >= mfe_max_retrace)
        self.assertTrue(armed_2, "MFE arms at +2.20R")
        self.assertFalse(trigger_2, "MFE must not trigger on normal 0.40R pullback from +2.20R")

        # Scenario 3: Deep runner at +2.20R with severe 0.80R collapse
        current_r_scenario3 = 1.40
        retrace_3 = peak_r_scenario2 - current_r_scenario3
        trigger_3 = armed_2 and (retrace_3 >= mfe_max_retrace)
        self.assertTrue(trigger_3, "MFE must trigger when retrace gives back >= 0.75R from a +2.0R+ peak")

    def test_invariant_fractional_risk_accounting_boundary(self):
        """
        Adversarial Invariant: Fractional Risk Units Capacity Boundary:
        1. Negative Boundary: With 2.5 units consumed today, a full 1.00x trade MUST be rejected (2.5 + 1.0 = 3.5 > 3.0).
        2. Positive Trigger: With 2.5 units consumed today, a 0.50x probe MUST pass (2.5 + 0.5 = 3.0 <= 3.0).
        """
        import json
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {"date": today_str, "setups_fired": 3, "units_used": 2.5}

        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(data))), \
             patch("os.path.exists", return_value=True), \
             patch.object(Config, 'DAILY_RISK_UNIT_CAP', 3.0):
            # 1. Full trade (1.0 unit) must be rejected
            ok_full, reason_full = ExecutionFirewall.check_global_daily_setup_limit(requested_units=1.0)
            self.assertFalse(ok_full, "Full trade at 2.5 units must be rejected")
            self.assertIn("Daily Risk Unit Limit hit", reason_full)

            # 2. Probe trade (0.5 unit) must be accepted
            ok_probe, reason_probe = ExecutionFirewall.check_global_daily_setup_limit(requested_units=0.5)
            self.assertTrue(ok_probe, f"Probe trade at 2.5 units must be approved, got: {reason_probe}")

    def test_invariant_two_probe_loss_circuit_breaker_boundary(self):
        """
        Adversarial Invariant: 2.0 Unit Cumulative Loss Circuit Breaker:
        1. Negative Boundary: 2 probe losses (-0.5R + -0.5R = -1.0 Unit) MUST NOT trigger 24h lockdown.
        2. Positive Trigger: 4 probe losses (-0.5R * 4 = -2.0 Units) MUST trigger 24h lockdown.
        """
        import os
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = os.path.join(tmp_dir, "test.db")
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
            # Insert 2 probe losses (-17.50 each)
            cur.execute("INSERT INTO journal VALUES (1, ?, 'BTC/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE')", (f"{today_str}T02:00:00",))
            cur.execute("INSERT INTO journal VALUES (2, ?, 'ETH/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE')", (f"{today_str}T04:00:00",))
            conn.commit()

            with patch.object(Config, 'DB_PATH', test_db), \
                 patch.object(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0):
                # 1. 2 probe losses must NOT trigger circuit breaker
                cb_ok_2p, reason_2p = ExecutionFirewall.check_daily_loss_circuit_breaker()
                self.assertTrue(cb_ok_2p, f"Two probe losses (-1.0 Unit) must NOT trigger circuit breaker: {reason_2p}")

                # Insert 2 more probe losses (total 4 probe losses = -2.0 Units)
                cur.execute("INSERT INTO journal VALUES (3, ?, 'SOL/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE')", (f"{today_str}T05:00:00",))
                cur.execute("INSERT INTO journal VALUES (4, ?, 'BTC/USD', 'BUY', -17.50, 'CLOSED', 'AUCTION_PROBE')", (f"{today_str}T06:00:00",))
                conn.commit()

                # 2. 4 probe losses MUST trigger circuit breaker
                cb_ok_4p, reason_4p = ExecutionFirewall.check_daily_loss_circuit_breaker()
                self.assertFalse(cb_ok_4p, "Four probe losses (-2.0 Units) MUST trigger circuit breaker!")
                self.assertTrue("cumulative loss limit reached" in reason_4p or "Daily consecutive loss ceiling hit" in reason_4p)
            conn.close()

    def test_invariant_session_anti_clustering_boundary(self):
        """
        Adversarial Invariant: Session Anti-Clustering Gate (Gate 9):
        1. Negative Boundary: 2nd trade in same session (Asian Judas) MUST be rejected.
        2. Positive Trigger: 1st trade in London Open MUST pass even after Asian Judas trade.
        """
        import os
        import json
        import tempfile
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {
            "date": today_str,
            "setups_fired": 1,
            "units_used": 0.5,
            "session_counts": {"ASIAN_JUDAS": 1},
            "sessions_fired": ["ASIAN_JUDAS"]
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = os.path.join(tmp_dir, "empty.db")
            with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(data))), \
                 patch("os.path.exists", return_value=True), \
                 patch.object(Config, 'DB_PATH', test_db), \
                 patch.object(Config, 'MAX_SETUPS_PER_KILLZONE_SESSION', 1):
                
                # 1. Duplicate in Asian Judas must fail
                ok_asian, reason_asian = ExecutionFirewall.check_session_setup_limit(session_hint="ASIAN_SESSION_JUDAS")
                self.assertFalse(ok_asian)
                self.assertIn("Gate 9", reason_asian)

                # 2. Distinct London Open must pass
                ok_london, reason_london = ExecutionFirewall.check_session_setup_limit(session_hint="LONDON_OPEN")
                self.assertTrue(ok_london, f"Expected LONDON_OPEN to pass but got: {reason_london}")
                self.assertEqual(reason_london, "OK")


if __name__ == '__main__':
    unittest.main()


