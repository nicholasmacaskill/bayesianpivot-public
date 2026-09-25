"""
Tier 1: Broker Safety & Execution Invariants Test Suite
======================================================
Strictly enforces AGENTS.md constraints across TradeLocker client & helpers.
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from src.clients.tl_client import TradeLockerHelper, TradeLockerClient
from src.engines.multi_account_funnel import align_lot_size


class TestBrokerInvariants(unittest.TestCase):
    def setUp(self):
        self.helper = TradeLockerHelper(
            email="test@upcomers.com",
            password="test_password",
            server="https://demo.tradelocker.com",
            base_url="https://demo.tradelocker.com"
        )
        self.helper.access_token = "mock_valid_jwt_token"
        self.helper.account_id = "mock_acc_123"

    def test_invariant_lot_size_alignment(self):
        """Invariant: Lot sizes must always align with broker step constraints (min 0.01, step 0.01)."""
        self.assertEqual(align_lot_size(0.1299999), 0.12)
        self.assertEqual(align_lot_size(0.005), 0.0) # Below minimum
        self.assertEqual(align_lot_size(0.254), 0.25)
        self.assertEqual(align_lot_size(1.0001), 1.0)

    @patch('requests.post')
    def test_invariant_zero_naked_orders_on_entry(self, mock_post):
        """Invariant: Every market order payload must contain valid Stop Loss."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"orderId": "12345"}
        mock_post.return_value = mock_resp

        # Call place_order with stop_loss and take_profit
        with patch.object(self.helper, 'get_open_positions', return_value=[]):
            self.helper.place_order(
                instrument_id=19965,
                side="buy",
                qty=0.15,
                stop_loss=76500.0,
                take_profit=77800.0
            )

        self.assertTrue(mock_post.called)
        sent_payload = mock_post.call_args[1]['json']
        
        # Invariant Assertions
        self.assertIn("stopLoss", sent_payload, "FATAL: Order payload missing stopLoss!")
        self.assertEqual(sent_payload["stopLoss"], 76500.0)
        self.assertEqual(sent_payload["stopLossType"], "absolute")
        self.assertIn("takeProfit", sent_payload, "FATAL: Order payload missing takeProfit!")
        self.assertEqual(sent_payload["takeProfit"], 77800.0)
        self.assertEqual(sent_payload["side"], "buy")
        self.assertEqual(sent_payload["type"], "market")

    @patch('requests.patch')
    @patch('requests.post')
    def test_invariant_bracket_modification_must_use_patch_never_stop_order(self, mock_post, mock_patch):
        """
        Invariant: Modifying Stop Loss or Take Profit MUST use PATCH /positions/{id}.
        It must NEVER call POST /orders with type: 'stop' (prevents duplicate naked orders).
        """
        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch_resp.json.return_value = {"success": True}
        mock_patch.return_value = mock_patch_resp

        res = self.helper.modify_position_bracket(
            position_id="pos_9999",
            stop_loss=76800.0,
            take_profit=78000.0
        )

        self.assertTrue(res)
        self.assertTrue(mock_patch.called, "FATAL: modify_position_bracket did NOT call PATCH!")
        self.assertFalse(mock_post.called, "FATAL: modify_position_bracket called POST /orders! This creates duplicate stop orders.")

        patch_url = mock_patch.call_args[0][0]
        self.assertIn("/positions/pos_9999", patch_url)
        patch_payload = mock_patch.call_args[1]['json']
        self.assertEqual(patch_payload.get("stopLoss"), 76800.0)
        self.assertEqual(patch_payload.get("stopLossType"), "absolute")
        self.assertEqual(patch_payload.get("takeProfit"), 78000.0)

    @patch('requests.delete')
    @patch('requests.post')
    def test_invariant_position_closure_must_use_delete_never_opposing_market_order(self, mock_post, mock_patch_delete):
        """
        Invariant: Terminating an open position MUST use DELETE /positions/{id}.
        It must NEVER place an opposing market order via POST /orders (which causes hedging duplicates).
        """
        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 200
        mock_del_resp.json.return_value = {"success": True}
        mock_patch_delete.return_value = mock_del_resp

        res = self.helper.close_position(position_id="pos_8888")

        self.assertTrue(res)
        self.assertTrue(mock_patch_delete.called, "FATAL: close_position did NOT call DELETE!")
        self.assertFalse(mock_post.called, "FATAL: close_position called POST /orders! In hedging accounts this opens opposing positions.")
        
        del_url = mock_patch_delete.call_args[0][0]
        self.assertIn("/positions/pos_8888", del_url)

    @patch('requests.delete')
    @patch('time.sleep')
    def test_invariant_close_position_retries_on_429_rate_limit(self, mock_sleep, mock_delete):
        """
        Invariant: Position termination via DELETE must handle HTTP 429 rate limit backoff
        and retry instead of immediately aborting and leaving orphaned unmanaged positions.
        """
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "5"}
        resp_429.text = "Rate limited"

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"success": True}

        mock_delete.side_effect = [resp_429, resp_200]

        res = self.helper.close_position(position_id="pos_429_test")
        self.assertTrue(res, "close_position should succeed after 429 backoff")
        self.assertEqual(mock_delete.call_count, 2, "Should attempt 2 DELETE requests (initial + retry)")
        mock_sleep.assert_called_with(15.0)


if __name__ == "__main__":
    unittest.main()
