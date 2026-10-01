import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timezone

from src.core.config import Config
from src.engines.alpha_sweep_scanner import AlphaSweepScanner
from src.clients.tl_client import TradeLockerHelper

class TestTieredTrailingAndSessionLock(unittest.TestCase):
    def setUp(self):
        patcher = patch("src.clients.telegram_notifier.TelegramNotifier._send_message")
        self.mock_tg = patcher.start()
        self.addCleanup(patch.stopall)
        self.scanner = AlphaSweepScanner()
        self.scanner.tl = MagicMock()
        self.scanner.fetch_data = MagicMock()
        self.scanner._load_active_trade_brackets = MagicMock(return_value={})
        self.scanner._active_trade_brackets = {
            'BTCUSD': {
                'symbol': 'BTC/USD',
                'side': 'sell',
                'entry_price': 77725.0,
                'stop_loss': 77925.0,
                'take_profit': 77000.0,
                'initial_r_dist': 200.0,
                'session': 'LONDON_KILLZONE',
                'tier': 0
            }
        }
        self.scanner._position_tiers = {}

    def test_modify_position_bracket_url_contains_account_id(self):
        """Verify modify_position_bracket properly formats PATCH URL with account_id."""
        helper = TradeLockerHelper("test@upcomers.com", "pass", "server", "https://api.tradelocker.com")
        helper.access_token = "valid_token"
        helper.account_id = "123456"
        
        with patch("requests.patch") as mock_patch:
            mock_patch.return_value.status_code = 200
            res = helper.modify_position_bracket("pos_999", stop_loss=77725.0)
            
            self.assertTrue(res)
            mock_patch.assert_called_once()
            call_url = mock_patch.call_args[0][0]
            self.assertEqual(call_url, "https://api.tradelocker.com/backend-api/trade/accounts/123456/positions/pos_999")
            call_json = mock_patch.call_args[1]["json"]
            self.assertEqual(call_json["stopLoss"], 77725.0)

    def test_tier1_break_even_trigger(self):
        """Verify Tier 1 Break-Even triggers when floating R >= 1.5R."""
        self.scanner.tl.get_open_positions.return_value = [{
            'id': 'pos_1',
            'symbol': 'BTC/USD',
            'side': 'SELL',
            'price': 77725.0,
            'stopLoss': 77925.0, # 200 pt initial risk
            'takeProfit': 77000.0,
            'qty': 0.1
        }]
        
        # Price is at 77400 -> Floating gain = +325 pts (+1.625R >= 1.5R)
        mock_df = pd.DataFrame([{'close': 77400.0}])
        self.scanner.fetch_data.return_value = mock_df

        self.scanner.check_and_trail_positions()
        
        # Should have updated SL to entry minus BE offset (77725.0 - 25.0 = 77700.0)
        self.scanner.tl.update_fleet_stop_loss.assert_called_once_with(
            new_stop_loss=77700.0,
            symbol='BTC/USD'
        )
        self.assertEqual(self.scanner._position_tiers.get('pos_1'), 1)

    def test_tier2_profit_lock_trigger_at_2_5r(self):
        """Verify Tier 2 locks in +1.0R in profit when floating R >= 2.5R."""
        self.scanner.tl.get_open_positions.return_value = [{
            'id': 'pos_2',
            'symbol': 'BTC/USD',
            'side': 'SELL',
            'price': 77725.0,
            'stopLoss': 77925.0, # 200 pt initial risk
            'takeProfit': 77000.0,
            'qty': 0.1
        }]
        
        # Price is at 77150 -> Floating gain = +575 pts (+2.875R >= 2.5R)
        mock_df = pd.DataFrame([{'close': 77150.0}])
        self.scanner.fetch_data.return_value = mock_df

        self.scanner.check_and_trail_positions()
        
        # For SELL, +1.0R locked stop = Entry (77725.0) - (1.0 * 200) = 77525.0
        self.scanner.tl.update_fleet_stop_loss.assert_called_once_with(
            new_stop_loss=77525.0,
            symbol='BTC/USD'
        )
        self.assertEqual(self.scanner._position_tiers.get('pos_2'), 2)

    def test_tier2_profit_lock_trigger_at_80_pct_tp_distance(self):
        """Verify Tier 2 locks in +1.0R in profit when reaching >= 80% TP progress."""
        self.scanner.tl.get_open_positions.return_value = [{
            'id': 'pos_3',
            'symbol': 'BTC/USD',
            'side': 'SELL',
            'price': 77725.0,
            'stopLoss': 77925.0, # 200 pt initial risk
            'takeProfit': 77000.0, # 725 pt TP distance
            'qty': 0.1
        }]
        
        # Price is at 77100 -> Gain = +625 pts (625 / 725 = 86.2% >= 80%)
        mock_df = pd.DataFrame([{'close': 77100.0}])
        self.scanner.fetch_data.return_value = mock_df

        self.scanner.check_and_trail_positions()
        
        self.scanner.tl.update_fleet_stop_loss.assert_called_once_with(
            new_stop_loss=77525.0,
            symbol='BTC/USD'
        )
        self.assertEqual(self.scanner._position_tiers.get('pos_3'), 2)

    def test_session_transition_lock(self):
        """Verify Asian session positions floating >= 1.0R are locked at Break-Even during London transition."""
        self.scanner._active_trade_brackets['BTCUSD'] = {
            'symbol': 'BTC/USD',
            'side': 'sell',
            'entry_price': 77725.0,
            'stop_loss': 77925.0,
            'take_profit': 77000.0,
            'initial_r_dist': 200.0,
            'session': 'ASIAN_SESSION_JUDAS',
            'tier': 0
        }
        
        self.scanner.tl.get_open_positions.return_value = [{
            'id': 'pos_4',
            'symbol': 'BTC/USD',
            'side': 'SELL',
            'price': 77725.0,
            'qty': 0.1
        }]
        
        # Price at 77500 -> +225 pts gain = +1.125R (>= 1.0R)
        mock_df = pd.DataFrame([{'close': 77500.0}])
        self.scanner.fetch_data.return_value = mock_df

        # Mock datetime to 07:05 UTC (London transition)
        with patch("src.engines.alpha_sweep_scanner.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 3, 7, 5, 0, tzinfo=timezone.utc)
            self.scanner.check_and_trail_positions()

        self.scanner.tl.update_fleet_stop_loss.assert_called_once_with(
            new_stop_loss=77700.0,
            symbol='BTC/USD'
        )
        self.assertEqual(self.scanner._position_tiers.get('pos_4'), 1)

if __name__ == '__main__':
    unittest.main()
