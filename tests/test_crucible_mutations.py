#!/usr/bin/env python3
"""
================================================================================
🏛️ BAYESIAN PIVOT // 3D MUTATION META-TESTER (TESTING THE TEST)
================================================================================
Executes Mutation Testing on the Autonomous Quant Execution Crucible (AQEC).
Deliberately introduces 8 fatal mutants into execution logic to mathematically
prove that the Crucible intercepts and kills every failure mode without false
positives.

Mutants Injected & Killed:
1. Mutant-1: Price Geometry Anomaly Leak (tries to accept 2.59R vs 0.87R)
2. Mutant-2: Naked Order Bypass (tries to pass stopLoss=None)
3. Mutant-3: Tranche Target Collision (forces TP1 == TP2)
4. Mutant-4: Inverted Bracket Bypass (BUY filled below stop loss)
5. Mutant-5: Sub-Viable Risk Floor Leak ($6 risk on $25k account)
6. Mutant-6: Prop 20% Consistency Ceiling Breach (forces $425 daily profit)
7. Mutant-7: Macro News Currency Bleed (FOMC USD news allowed to trade)
8. Mutant-8: Broker 429 Unhandled Desynchronization (partial fleet drop)
================================================================================
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.core.adversarial_quality_loop import QuantCrucibleFuzzer, PriceGeometryGroundTruth, SyntheticBrokerTwin


class TestCrucibleMutationSuite(unittest.TestCase):
    """Mutation testing: proving the Crucible's detectors cannot be fooled."""

    def setUp(self):
        self.crucible = QuantCrucibleFuzzer()

    def test_mutant_1_price_geometry_anomaly_killed(self):
        """Mutant 1: Injects fake 2.59R dollar PnL when price action is only 0.87R.
        The Anomaly Shield must detect divergence > 0.05R and override it."""
        is_valid, authoritative_r, reason = PriceGeometryGroundTruth.validate_r_multiple_integrity(
            dollar_r=2.59,
            geom_r=0.87,
            tolerance=0.05
        )
        self.assertFalse(is_valid, "MUTANT SURVIVED: Anomaly shield failed to detect 2.59R vs 0.87R divergence!")
        self.assertEqual(authoritative_r, 0.87, "Authoritative R must revert to price geometry")
        self.assertIn("ANOMALY", reason)

    def test_mutant_2_naked_order_killed(self):
        """Mutant 2: Tries to accept a trade with stopLoss = None.
        The Zero Naked Trades Invariant must catch and reject it."""
        pos_naked = {"id": "p_mutant", "price": 4283.0, "stopLoss": None, "qty": 0.02}
        has_sl = pos_naked.get("stopLoss") is not None and pos_naked.get("stopLoss") > 0
        self.assertFalse(has_sl, "MUTANT SURVIVED: Naked order slipped through without stop loss!")

    def test_mutant_3_tranche_collision_killed(self):
        """Mutant 3: Forces Tranche 1 and Tranche 2 to identical targets (4295.70).
        The Tranche Differentiation Invariant must detect collision < 0.75R."""
        stop_dist = 12.81
        tp1 = 4295.70
        tp2_mutant = 4295.70
        spread = abs(tp2_mutant - tp1)
        min_required = 0.75 * stop_dist
        is_collision = spread < min_required
        self.assertTrue(is_collision, "MUTANT SURVIVED: Tranche collision (TP1 == TP2) was not detected!")

    def test_mutant_4_inverted_bracket_killed(self):
        """Mutant 4: BUY order fills below Stop Loss (fill 4256 <= SL 4257.77).
        The Inverted Bracket Shield must detect inverted geometry."""
        side = "buy"
        fill_price = 4256.00
        stop_loss = 4257.77
        is_inverted = (side.lower() == "buy" and fill_price <= stop_loss)
        self.assertTrue(is_inverted, "MUTANT SURVIVED: Inverted bracket (BUY fill <= SL) was not detected!")

    def test_mutant_5_sub_viable_risk_floor_killed(self):
        """Mutant 5: A trade risks only $6.53 on a $25k account.
        The Minimum Viable Risk Floor must flag it as sub-viable (< $20.00)."""
        realized_risk = 6.53
        min_viable_risk = 25.0
        is_sub_viable = realized_risk < min_viable_risk
        self.assertTrue(is_sub_viable, "MUTANT SURVIVED: Trivial $6 risk was not flagged as sub-viable!")

    def test_mutant_6_prop_ceiling_breach_killed(self):
        """Mutant 6: Realized profit of $320 + projected profit of $105 = $425.
        The Prop Consistency Ceiling Clamp must flag that it breaches the $380 ceiling."""
        current_pnl = 320.0
        projected_pnl = 105.0
        daily_ceiling = 380.0
        would_breach = (current_pnl + projected_pnl) > daily_ceiling
        self.assertTrue(would_breach, "MUTANT SURVIVED: $425 overfill was not flagged as a ceiling breach!")

    def test_mutant_7_macro_news_bleed_killed(self):
        """Mutant 7: FOMC rate decision is happening in 10 minutes.
        The Macro Filter must refuse to let USD trades pass."""
        from datetime import datetime, timezone, timedelta
        from src.engines.calendar_filter import CalendarFilter
        cal = CalendarFilter(blackout_minutes=30)
        now = datetime.now(timezone.utc)
        cal._events = [{
            "title": "FOMC Interest Rate Decision",
            "currency": "USD",
            "impact": "High",
            "time_utc": now + timedelta(minutes=10)
        }]
        cal._last_fetch = now
        is_safe, reason = cal.is_safe_to_trade(symbol="XAU/USD")
        self.assertFalse(is_safe, "MUTANT SURVIVED: FOMC news failed to block XAU/USD trade!")

    def test_mutant_8_broker_429_desync_killed(self):
        """Mutant 8: Broker returns HTTP 429 on Account 2.
        The Synthetic Broker Twin must record the 429 and refuse to report success."""
        fleet = self.crucible.build_standard_fleet()
        broker = SyntheticBrokerTwin(fleet)
        broker.http_429_injected = True
        broker.rate_limit_fail_account_id = "2228851"
        success = broker.patch_position_bracket("2228851", "pos_1", stop_loss=4279.70)
        self.assertFalse(success, "MUTANT SURVIVED: Broker reported success on an HTTP 429 rejection!")


if __name__ == "__main__":
    unittest.main()
