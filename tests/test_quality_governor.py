import os
import unittest
import tempfile
from unittest.mock import MagicMock, patch
from src.core.quality_governor import QualityGovernor

class TestQualityGovernor(unittest.TestCase):

    def setUp(self):
        self.governor = QualityGovernor()

    def test_symbol_translations_pass(self):
        """Verify standard crypto symbols map cleanly without errors."""
        passed, issues = self.governor.check_symbol_translations()
        self.assertTrue(passed, f"Symbol translations failed: {issues}")
        self.assertEqual(len(issues), 0)

    def test_schema_integrity_pass(self):
        """Verify database tables and columns match expected production schema."""
        passed, issues = self.governor.check_schema_integrity()
        self.assertTrue(passed, f"Schema integrity failed: {issues}")
        self.assertEqual(len(issues), 0)

    def test_silent_error_sentry_detects_bad_symbol_and_timeouts(self):
        """Verify log sentry detects repeated silent failures above threshold."""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            # Write repetitive BadSymbol and 401 errors
            f.write(f"{now_str} - [ERROR] - BadSymbol: coinbase does not have market symbol BTCUSD\n" * 5)
            f.write(f"{now_str} - [WARNING] - 401 Unauthorized for s79qv3xetj@upcomers.com\n" * 16)
            temp_path = f.name

        try:
            test_gov = QualityGovernor(log_path=temp_path)
            clean, issues = test_gov.scan_silent_error_rate(lookback_lines=50)
            self.assertFalse(clean)
            self.assertTrue(any("BadSymbol" in iss for iss in issues))
            self.assertTrue(any("401 Unauthorized" in iss for iss in issues))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_active_position_invariants_detect_missing_sl(self):
        """Verify audit flags positions missing protective stop loss."""
        mock_tl = MagicMock()
        mock_tl.get_open_positions.return_value = [
            {
                "id": "pos_unsafe",
                "symbol": "BTCUSD",
                "price": 76682.0,
                "stopLoss": None, # RULE 6 BREACH!
                "stopLossOrderId": None,
                "takeProfit": 75939.0,
                "pnl": 10.0,
                "qty": 0.1
            }
        ]

        clean, issues = self.governor.audit_active_positions(tl_client=mock_tl)
        self.assertFalse(clean)
        self.assertTrue(any("RULE 6 VIOLATION" in iss for iss in issues))

    def test_active_position_invariants_pass_when_bracket_order_id_present(self):
        """Verify audit passes when SL price is None/0 but stopLossOrderId is attached on broker."""
        mock_tl = MagicMock()
        mock_tl.get_open_positions.return_value = [
            {
                "id": "pos_protected_via_order_id",
                "symbol": "BTCUSD",
                "price": 86383.0,
                "stopLoss": None, # Price float temporarily unresolved due to rate limit
                "takeProfit": None,
                "stopLossOrderId": "288230376185007874", # VERIFIED ON BROKER BOOK!
                "takeProfitOrderId": "288230376185007876",
                "pnl": 5.0,
                "qty": 0.14
            }
        ]

        clean, issues = self.governor.audit_active_positions(tl_client=mock_tl)
        self.assertTrue(clean, f"Expected clean audit but got: {issues}")
        self.assertEqual(len(issues), 0)

    def test_active_position_invariants_detect_scale_out_overwrite(self):
        """Verify audit flags when two tranches share identical TP (overwritten bracket)."""
        mock_tl = MagicMock()
        mock_tl.get_open_positions.return_value = [
            {
                "id": "pos_T1",
                "symbol": "BTCUSD",
                "side": "SELL",
                "price": 76682.0,
                "stopLoss": 76859.0,
                "takeProfit": 75939.0, # OVERWRITTEN!
                "pnl": 10.0,
                "qty": 0.1
            },
            {
                "id": "pos_T2",
                "symbol": "BTCUSD",
                "side": "SELL",
                "price": 76682.0,
                "stopLoss": 76859.0,
                "takeProfit": 75939.0,
                "pnl": 10.0,
                "qty": 0.1
            }
        ]

        clean, issues = self.governor.audit_active_positions(tl_client=mock_tl)
        self.assertFalse(clean)
        self.assertTrue(any("SCALE-OUT BRACKET OVERWRITE" in iss for iss in issues))

    def test_active_position_invariants_detect_unlocked_break_even_at_1_5r(self):
        """Verify audit flags when trade is >= +1.5R but stop loss is not at Break-Even."""
        mock_tl = MagicMock()
        mock_tl.get_open_positions.return_value = [
            {
                "id": "pos_profit",
                "symbol": "BTCUSD",
                "side": "SELL",
                "price": 76682.0,
                "stopLoss": 76859.0, # Still at initial SL (177 pt risk)!
                "takeProfit": 75939.0,
                "pnl": 200.0, # 177 * 0.42 = $74.34 risk -> $200 pnl = 2.69R!
                "qty": 0.42
            }
        ]

        with patch("src.core.quality_governor._load_json_resilient", return_value={}):
            clean, issues = self.governor.audit_active_positions(tl_client=mock_tl)
            self.assertFalse(clean)
            self.assertTrue(any("BREAK-EVEN INVARIANT BREACH" in iss for iss in issues))

    def test_contract_size_invariants_across_all_asset_classes(self):
        """Verify Config.get_contract_size returns correct multiplier for all asset types."""
        from src.core.config import Config
        # Crypto
        self.assertEqual(Config.get_contract_size("BTC/USD"), 1.0)
        self.assertEqual(Config.get_contract_size("BTCUSD"), 1.0)
        self.assertEqual(Config.get_contract_size("ETH/USD"), 1.0)
        self.assertEqual(Config.get_contract_size("ETHUSD"), 1.0)
        self.assertEqual(Config.get_contract_size("SOL/USD"), 1.0)
        self.assertEqual(Config.get_contract_size("SOLUSD"), 1.0)
        # Metals
        self.assertEqual(Config.get_contract_size("XAU/USD"), 100.0)
        self.assertEqual(Config.get_contract_size("GOLD"), 100.0)
        self.assertEqual(Config.get_contract_size("XAG/USD"), 5000.0)
        # Forex
        self.assertEqual(Config.get_contract_size("EUR/USD"), 100000.0)
        self.assertEqual(Config.get_contract_size("GBP/USD"), 100000.0)

    def test_active_position_invariants_allows_identical_tp_on_different_accounts(self):
        """Negative boundary test: Positions on different accounts with identical TP must NOT trigger overwrite alert."""
        mock_tl = MagicMock()
        mock_tl.get_open_positions.return_value = [
            {
                "id": "pos_acc1",
                "account_id": "acc_1",
                "symbol": "BTCUSD",
                "side": "SELL",
                "price": 84706.32,
                "stopLoss": 84672.22,
                "takeProfit": 83107.27,
                "pnl": 20.0,
                "qty": 0.05
            },
            {
                "id": "pos_acc2",
                "account_id": "acc_2",
                "symbol": "BTCUSD",
                "side": "SELL",
                "price": 84697.22,
                "stopLoss": 84672.22,
                "takeProfit": 83107.27,
                "pnl": 80.0,
                "qty": 0.20
            }
        ]

        clean, issues = self.governor.audit_active_positions(tl_client=mock_tl)
        self.assertTrue(clean)
        self.assertEqual(issues, [])

    def test_silent_error_sentry_rate_limit_threshold(self):
        """Verify that rate limited alerts trigger only when reaching the threshold (8 occurrences)."""
        import tempfile
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            # 4 occurrences (below threshold of 8)
            for _ in range(4):
                tf.write(f"{now_str} - [WARNING] - (Worker) - Rate limited (HTTP 429)\n")
            temp_path = tf.name

        gov = QualityGovernor(log_path=temp_path)
        clean, issues = gov.scan_silent_error_rate()
        os.unlink(temp_path)
        self.assertTrue(clean)
        self.assertEqual(issues, [])

        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            # 8 occurrences (meets threshold)
            for _ in range(8):
                tf.write(f"{now_str} - [WARNING] - (Worker) - Rate limited (HTTP 429)\n")
            temp_path = tf.name

        gov = QualityGovernor(log_path=temp_path)
        clean, issues = gov.scan_silent_error_rate()
        os.unlink(temp_path)
        self.assertFalse(clean)
        self.assertTrue(any("Rate limited" in iss for iss in issues))

if __name__ == "__main__":
    unittest.main()

