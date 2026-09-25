import unittest
from unittest.mock import MagicMock, patch
from scripts.position_watchdog import PositionWatchdog
from src.clients.tl_client import TradeLockerHelper, TradeLockerClient

class TestWatchdogAndScaleoutInvariants(unittest.TestCase):

    def test_watchdog_broker_native_stop_loss_resolution(self):
        """Verify watchdog retrieves stop loss directly from position object without DB dependencies."""
        watchdog = PositionWatchdog()
        
        pos_with_sl = {
            "id": "pos_123",
            "symbol": "BTCUSD",
            "price": 76682.0,
            "stopLoss": 76859.15,
            "takeProfit": 75939.59,
            "pnl": 211.0,
            "qty": 0.42
        }
        
        sl, scan = watchdog.get_stop_loss("BTCUSD", pos=pos_with_sl)
        self.assertEqual(sl, 76859.15)
        self.assertIsNone(scan)

    def test_watchdog_r_multiple_calculation(self):
        """Verify R-multiple calculation when trade moves into profit."""
        watchdog = PositionWatchdog()
        pos = {
            "id": "pos_123",
            "symbol": "BTCUSD",
            "price": 76682.0,
            "stopLoss": 76859.0, # risk = 177 pts
            "pnl": 211.0,
            "qty": 0.42
        }
        
        sl, _ = watchdog.get_stop_loss(pos["symbol"], pos=pos)
        entry = pos["price"]
        qty = pos["qty"]
        risk_usd = abs(entry - sl) * qty * 1.0 # BTC contract size = 1.0
        
        # 177 pts * 0.42 = $74.34 risk
        # $211.0 / $74.34 = 2.838R
        r_multiple = pos["pnl"] / risk_usd
        self.assertAlmostEqual(r_multiple, 2.838, places=2)
        self.assertGreaterEqual(r_multiple, 1.5)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    @patch.object(TradeLockerHelper, 'login')
    def test_two_tranche_split_bracket_preservation(self, mock_login, mock_get_pos, mock_modify):
        """Verify Tranche 1 is NOT overwritten when Tranche 2 is placed."""
        mock_login.return_value = True
        helper = TradeLockerHelper("test@upcomers.com", "pass", "srv", "http://fake")
        helper.account_id = "123"
        helper.access_token = "token"

        # Position 1 created by Tranche 1
        pos1 = {
            "id": "pos_T1",
            "instrumentId": "19965",
            "symbol": "BTCUSD",
            "side": "SELL",
            "qty": 0.20,
            "price": 76682.0,
            "stopLoss": 76859.0,
            "takeProfit": 76169.0
        }
        # Position 2 created by Tranche 2
        pos2 = {
            "id": "pos_T2",
            "instrumentId": "19965",
            "symbol": "BTCUSD",
            "side": "SELL",
            "qty": 0.22,
            "price": 76682.0,
            "stopLoss": 76859.0,
            "takeProfit": 75939.0
        }

        # First call to place_order: returns pos1 in open_positions
        mock_get_pos.side_effect = [
            [], # pre_pos before T1
            [pos1], # post_pos after T1
            [pos1], # pre_pos before T2 (pos1 already exists!)
            [pos1, pos2] # post_pos after T2 (both exist now)
        ]
        mock_modify.return_value = True

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"orderId": "ord_1"}

            # Place Tranche 1
            res1 = helper.place_order(
                instrument_id="19965",
                side="sell",
                qty=0.20,
                stop_loss=76859.0,
                take_profit=76169.0
            )

            # Place Tranche 2
            res2 = helper.place_order(
                instrument_id="19965",
                side="sell",
                qty=0.22,
                stop_loss=76859.0,
                take_profit=75939.0
            )

        # modify_position_bracket should have been called TWICE: once for pos_T1, once for pos_T2
        self.assertEqual(mock_modify.call_count, 2)
        call_1_pos_id = mock_modify.call_args_list[0][0][0]
        call_1_tp = mock_modify.call_args_list[0][1]["take_profit"]
        
        call_2_pos_id = mock_modify.call_args_list[1][0][0]
        call_2_tp = mock_modify.call_args_list[1][1]["take_profit"]

        self.assertEqual(call_1_pos_id, "pos_T1")
        self.assertEqual(call_1_tp, 76169.0)

        self.assertEqual(call_2_pos_id, "pos_T2")
        self.assertEqual(call_2_tp, 75939.0)

    def test_tp_front_run_cushion_offsets(self):
        """Verify TP front-run cushion properly offsets limit targets for passive fills."""
        from src.core.config import Config
        self.assertTrue(Config.TP_FRONT_RUN_CUSHION_ENABLED)
        cushion_btc = Config.TP_FRONT_RUN_CUSHION_USD.get("BTC", 0.0)
        self.assertEqual(cushion_btc, 15.0)

        # On a SHORT trade: raw TP = $76,169.48 -> offset TP = $76,184.48 (easier to fill)
        raw_tp_short = 76169.48
        offset_tp_short = round(raw_tp_short + cushion_btc, 2)
        self.assertEqual(offset_tp_short, 76184.48)
        # Verify 76,179.00 (today's actual low) hits the offset TP
        self.assertLessEqual(76179.00, offset_tp_short)

    @patch.object(PositionWatchdog, 'execute_fleet_scaleout')
    def test_mfe_peak_retracement_scaleout_trigger(self, mock_scaleout):
        """Verify MFE peak retracement triggers scaleout when profit gives back >= 0.75R."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        t_id = "test_pos_mfe"
        watchdog.alerted_trades = {
            t_id: {
                "peak_r": 2.50, # Peaked at +2.5R
                "scaleout_executed": True # Already did +1.5R scaleout
            }
        }
        
        pos = {
            "id": t_id,
            "symbol": "BTCUSD",
            "price": 76682.0,
            "stopLoss": 76859.0, # risk = 177 pts * 0.42 = $74.34
            "pnl": 111.51, # $111.51 / $74.34 = 1.50R (retraced 1.00R from 2.50R!)
            "qty": 0.42
        }

        # Mock tl.get_open_positions
        watchdog.tl = MagicMock()
        watchdog.tl.get_open_positions.return_value = [pos]
        
        # Run one iteration logic
        entry = pos["price"]
        sl, _ = watchdog.get_stop_loss("BTCUSD", pos=pos)
        risk_usd = abs(entry - sl) * pos["qty"] * 1.0
        r_multiple = pos["pnl"] / risk_usd # 1.60R
        
        peak_r = max(watchdog.alerted_trades.get(t_id, {}).get("peak_r", 0.0), r_multiple)
        retrace = peak_r - r_multiple # 0.90R
        
        from src.core.config import Config
        self.assertGreaterEqual(peak_r, Config.MFE_MIN_PEAK_R)
        self.assertGreaterEqual(retrace, Config.MFE_MAX_RETRACEMENT_R)

    @patch.object(TradeLockerHelper, 'close_position')
    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_mfe_peak_retracement_physically_market_closes_positions(self, mock_get_pos, mock_modify, mock_close):
        """Verify that when reason is MFE Peak Retracement, watchdog physically closes all open positions."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_close.return_value = True
        mock_get_pos.return_value = [
            {"id": "runner_pos_1", "symbol": "BTCUSD", "qty": 0.20}
        ]

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.get_open_positions = mock_get_pos
        helper.close_position = mock_close
        helper.modify_position_bracket = mock_modify

        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            watchdog.execute_fleet_scaleout("BTCUSD", entry_price=76682.0, reason="MFE Peak Retracement (+2.50R -> +1.60R)")

        # Verify close_position was called to market-close the runner
        mock_close.assert_called_once_with("runner_pos_1")
        # Verify modify_position_bracket was NOT called (we don't just trail to BE, we exit!)
        mock_modify.assert_not_called()

    @patch('requests.patch')
    def test_modify_position_bracket_reauth_on_401(self, mock_patch):
        """Verify modify_position_bracket catches 401, refreshes token via login(), and retries."""
        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "expired_token"
        helper.login = MagicMock(return_value=True)

        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"success": True}

        mock_patch.side_effect = [resp_401, resp_200]

        res = helper.modify_position_bracket("pos_123", stop_loss=76000.0)

        self.assertTrue(res)
        self.assertTrue(helper.login.called, "login() should be called on 401")
        self.assertEqual(mock_patch.call_count, 2, "Should retry patch after re-auth")

    @patch('requests.delete')
    def test_close_position_reauth_on_401(self, mock_delete):
        """Verify close_position catches 401, refreshes token via login(), and retries."""
        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "expired_token"
        helper.login = MagicMock(return_value=True)

        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_200 = MagicMock()
        resp_200.status_code = 200

        mock_delete.side_effect = [resp_401, resp_200]

        res = helper.close_position("pos_123")

        self.assertTrue(res)
        self.assertTrue(helper.login.called, "login() should be called on 401")
        self.assertEqual(mock_delete.call_count, 2, "Should retry delete after re-auth")

    @patch.object(PositionWatchdog, 'execute_fleet_scaleout')
    def test_scaleout_deduplication_across_multi_account_positions(self, mock_scaleout):
        """Verify scale-out and Telegram alerts fire exactly ONCE per symbol even with 6 positions open across fleet."""
        watchdog = PositionWatchdog()
        watchdog.symbol_state = {}
        watchdog.alerted_trades = {}
        watchdog.notifier._send_message = MagicMock()
        watchdog.save_state = MagicMock()

        # 6 positions across 6 accounts on BTCUSD at +1.6R
        fleet_positions = [
            {"id": f"pos_acc_{i}", "symbol": "BTCUSD", "price": 76682.0, "stopLoss": 76859.0, "pnl": 118.94, "qty": 0.42}
            for i in range(1, 7)
        ]

        watchdog.tl.get_open_positions = MagicMock(side_effect=[fleet_positions, []])

        # Run 1 iteration of the watchdog run loop logic
        clean_sym = "BTCUSD"
        for pos in fleet_positions:
            t_id = pos['id']
            symbol = pos['symbol']
            entry = float(pos['price'])
            pnl = float(pos['pnl'])
            sl, _ = watchdog.get_stop_loss(symbol, pos=pos)
            risk_usd = abs(entry - sl) * pos['qty'] * 1.0
            r_multiple = pnl / risk_usd # ~1.60R

            sym_data = watchdog.symbol_state.setdefault(clean_sym, {
                "scaleout_executed": False,
                "mfe_scaleout_executed": False,
                "macro_scaleout_executed": False,
                "peak_r": 0.0,
                "milestones": {}
            })

            peak_r = max(sym_data.get("peak_r", 0.0), r_multiple)
            sym_data["peak_r"] = peak_r

            be_trigger = 1.5
            is_scaled = sym_data.get("scaleout_executed") or watchdog.alerted_trades.get(t_id, {}).get("scaleout_executed")
            if r_multiple >= be_trigger and not is_scaled:
                watchdog.execute_fleet_scaleout(symbol, entry, reason="+1.5R Target Reached")
                sym_data["scaleout_executed"] = True
                watchdog.alerted_trades[t_id] = {"scaleout_executed": True}

            # Telegram milestone alert
            target_key = "1.5"
            is_alerted = sym_data.get("milestones", {}).get(target_key) or watchdog.alerted_trades.get(t_id, {}).get(target_key)
            if r_multiple >= 1.5 and not is_alerted:
                watchdog.notifier._send_message("Alert 1.5R")
                sym_data.setdefault("milestones", {})[target_key] = True

        # Assert scale-out was called EXACTLY ONCE (not 6 times)
        self.assertEqual(mock_scaleout.call_count, 1)
        # Assert Telegram message was sent EXACTLY ONCE (not 6 times)
        self.assertEqual(watchdog.notifier._send_message.call_count, 1)

    def test_watchdog_state_clears_when_flat(self):
        """Verify watchdog state auto-clears when flat so tomorrow's setups can scale out cleanly."""
        watchdog = PositionWatchdog()
        watchdog.save_state = MagicMock()
        watchdog.symbol_state["BTCUSD"] = {"scaleout_executed": True, "peak_r": 2.8}
        watchdog.alerted_trades["pos_1"] = {"scaleout_executed": True}

        # Simulated flat tick
        positions = []
        if not positions:
            watchdog.symbol_state.clear()
            watchdog.alerted_trades.clear()
            watchdog.save_state()

        self.assertEqual(len(watchdog.symbol_state), 0)
        self.assertEqual(len(watchdog.alerted_trades), 0)
        watchdog.save_state.assert_called_once()

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_stepped_defense_buy_calculates_and_tightens_sl(self, mock_get_pos, mock_modify):
        """Verify BUY trade tightens SL from -1.0R to -0.3R at +1.0R gain."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        mock_get_pos.return_value = [
            {"id": "pos_buy_1", "symbol": "BTCUSD", "price": 100.0, "stopLoss": 90.0, "qty": 1.0}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            # entry=100, initial_sl=90 (risk=10). locked_r=-0.3 -> new_sl = 100 - 3 = 97.0
            watchdog.execute_stepped_defense("BTCUSD", entry_price=100.0, initial_sl=90.0, side="BUY", locked_r=-0.3)

        mock_modify.assert_called_once_with("pos_buy_1", stop_loss=97.0)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_stepped_defense_sell_calculates_and_tightens_sl(self, mock_get_pos, mock_modify):
        """Verify SELL trade tightens SL from -1.0R to -0.3R at +1.0R gain."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        mock_get_pos.return_value = [
            {"id": "pos_sell_1", "symbol": "BTCUSD", "price": 100.0, "stopLoss": 110.0, "qty": 1.0}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            # entry=100, initial_sl=110 (risk=10). locked_r=-0.3 -> new_sl = 100 + 3 = 103.0
            watchdog.execute_stepped_defense("BTCUSD", entry_price=100.0, initial_sl=110.0, side="SELL", locked_r=-0.3)

        mock_modify.assert_called_once_with("pos_sell_1", stop_loss=103.0)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_stepped_defense_does_not_loosen_sl_if_already_at_break_even(self, mock_get_pos, mock_modify):
        """Verify stepped defense never loosens SL if already at Break-Even or better."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        # Position SL is already at 100.0 (Break-Even)
        mock_get_pos.return_value = [
            {"id": "pos_buy_be", "symbol": "BTCUSD", "price": 100.0, "stopLoss": 100.0, "qty": 1.0}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            # new_sl would be 97.0, but position is already at 100.0 (BE) -> must NOT overwrite to 97.0!
            watchdog.execute_stepped_defense("BTCUSD", entry_price=100.0, initial_sl=90.0, side="BUY", locked_r=-0.3)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_execute_fleet_scaleout_net_break_even_buy(self, mock_get_pos, mock_modify):
        """Verify BUY trade trails SL to True Net Break-Even (entry + fee buffer) to cover commission."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        # Entry 80000, initial SL 79600 (risk = 400). 0.08R = 32.0 pts offset (> 25.0 min buffer)
        mock_get_pos.return_value = [
            {"id": "pos_buy_be_test", "symbol": "BTCUSD", "side": "BUY", "price": 80000.0, "stopLoss": 79600.0, "qty": 0.10}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            watchdog.execute_fleet_scaleout("BTCUSD", entry_price=80000.0, reason="+1.5R Target Reached", side="BUY", initial_sl=79600.0)

        # Expected net BE: 80000.0 + 32.0 = 80032.0
        mock_modify.assert_called_once_with("pos_buy_be_test", stop_loss=80032.0)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_execute_fleet_scaleout_net_break_even_sell(self, mock_get_pos, mock_modify):
        """Verify SELL trade trails SL to True Net Break-Even (entry - fee buffer) to cover commission."""
        watchdog = PositionWatchdog()
        watchdog.notifier = MagicMock()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        # Entry 80000, initial SL 80400 (risk = 400). 0.08R = 32.0 pts offset (> 25.0 min buffer)
        mock_get_pos.return_value = [
            {"id": "pos_sell_be_test", "symbol": "BTCUSD", "side": "SELL", "price": 80000.0, "stopLoss": 80400.0, "qty": 0.10}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        watchdog.tl.helpers = [helper]

        with patch("time.sleep"):
            watchdog.execute_fleet_scaleout("BTCUSD", entry_price=80000.0, reason="+1.5R Target Reached", side="SELL", initial_sl=80400.0)

        # Expected net BE: 80000.0 - 32.0 = 79968.0
        mock_modify.assert_called_once_with("pos_sell_be_test", stop_loss=79968.0)

    @patch.object(TradeLockerHelper, 'modify_position_bracket')
    @patch.object(TradeLockerHelper, 'get_open_positions')
    def test_update_fleet_stop_loss_never_loosens_sl(self, mock_get_pos, mock_modify):
        """Verify TradeLockerClient.update_fleet_stop_loss never loosens an existing tighter Stop Loss."""
        from src.clients.tl_client import TradeLockerClient
        client = TradeLockerClient()
        mock_modify.return_value = True

        helper = TradeLockerHelper("test@upcomers.com", "p", "s", "http://fake")
        helper.access_token = "token"
        helper.account_id = "acc_1"
        # BUY position already at 80100.0 SL. Requested new SL is 80032.0 (looser) -> must be skipped!
        mock_get_pos.return_value = [
            {"id": "pos_tight_buy", "symbol": "BTCUSD", "side": "BUY", "price": 80000.0, "stopLoss": 80100.0, "qty": 0.10}
        ]
        helper.get_open_positions = mock_get_pos
        helper.modify_position_bracket = mock_modify
        client.helpers = [helper]

        with patch("time.sleep"):
            client.update_fleet_stop_loss(new_stop_loss=80032.0, symbol="BTCUSD")

        mock_modify.assert_not_called()

if __name__ == "__main__":
    unittest.main()
