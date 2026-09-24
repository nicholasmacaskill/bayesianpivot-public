#!/usr/bin/env python3
"""
Test Suite: Autonomous Adversarial Quality Loop (Quant Crucible)
Wired into tests/run_bulletproof_harness.py
"""

import unittest
from src.core.adversarial_quality_loop import QuantCrucibleFuzzer, PriceGeometryGroundTruth


class TestAutonomousAdversarialQualityLoop(unittest.TestCase):
    """Executes the full 10-point Adversarial Crucible suite."""

    def setUp(self):
        self.crucible = QuantCrucibleFuzzer()

    def test_scenario_1_sub_2r_reversal_no_panic(self):
        res = self.crucible.test_scenario_1_sub_2r_reversal_no_panic()
        self.assertTrue(res["passed"])

    def test_scenario_2_deep_win_retracement_protection(self):
        res = self.crucible.test_scenario_2_deep_win_retracement_protection()
        self.assertTrue(res["passed"])

    def test_scenario_3_stepped_defense_in_place_patch(self):
        res = self.crucible.test_scenario_3_stepped_defense_in_place_patch()
        self.assertTrue(res["passed"])

    def test_scenario_4_cross_account_lot_size_isolation(self):
        res = self.crucible.test_scenario_4_cross_account_lot_size_isolation()
        self.assertTrue(res["passed"])

    def test_scenario_5_asset_multiplier_invariance(self):
        res = self.crucible.test_scenario_5_asset_multiplier_invariance()
        self.assertTrue(res["passed"])

    def test_scenario_6_prop_20pct_consistency_ceiling(self):
        res = self.crucible.test_scenario_6_prop_20pct_consistency_ceiling()
        self.assertTrue(res["passed"])

    def test_scenario_7_broker_429_adaptive_resilience(self):
        res = self.crucible.test_scenario_7_broker_429_adaptive_resilience()
        self.assertTrue(res["passed"])

    def test_scenario_8_zero_naked_trades_invariant(self):
        res = self.crucible.test_scenario_8_zero_naked_trades_invariant()
        self.assertTrue(res["passed"])

    def test_scenario_9_price_geometry_vs_dollar_anomaly_detection(self):
        res = self.crucible.test_scenario_9_price_geometry_vs_dollar_anomaly_detection()
        self.assertTrue(res["passed"])

    def test_scenario_10_pre_macro_blackout_currency_isolation(self):
        res = self.crucible.test_scenario_10_pre_macro_blackout_currency_isolation()
        self.assertTrue(res["passed"])

    def test_scenario_11_fill_slippage_and_bracket_reanchoring(self):
        res = self.crucible.test_scenario_11_fill_slippage_and_bracket_reanchoring()
        self.assertTrue(res["passed"])

    def test_scenario_12_tranche_differentiation_minimum_spread(self):
        res = self.crucible.test_scenario_12_tranche_differentiation_minimum_spread()
        self.assertTrue(res["passed"])

    def test_scenario_13_minimum_viable_dollar_risk_floor(self):
        res = self.crucible.test_scenario_13_minimum_viable_dollar_risk_floor()
        self.assertTrue(res["passed"])

    def test_scenario_14_partial_tranche_asymmetry_resilience(self):
        res = self.crucible.test_scenario_14_partial_tranche_asymmetry_resilience()
        self.assertTrue(res["passed"])

    def test_scenario_15_inverted_bracket_negative_slippage_shield(self):
        res = self.crucible.test_scenario_15_inverted_bracket_negative_slippage_shield()
        self.assertTrue(res["passed"])

    def test_scenario_16_dynamic_room_under_ceiling_sizing(self):
        res = self.crucible.test_scenario_16_dynamic_room_under_ceiling_sizing()
        self.assertTrue(res["passed"])

    def test_full_crucible_run_all_summary(self):
        summary = self.crucible.run_all()
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["total_scenarios"], 16)
        self.assertEqual(summary["passed_scenarios"], 16)


if __name__ == "__main__":
    unittest.main()
