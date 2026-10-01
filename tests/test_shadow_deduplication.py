import os
import pytest
from src.engines.counterfactual_tracker import CounterfactualTracker
from src.core.database import get_db_connection

def test_shadow_deduplication_invariant():
    """Asserts that duplicate shadow trades for the same (account, symbol, pattern, direction) are rejected while OPEN."""
    tracker = CounterfactualTracker()
    test_account = "TEST_DEDUP_ACCT"
    test_symbol = "BTC/USD"
    test_pattern = "TEST_TURTLE_SOUP_DEDUP"
    test_direction = "BUY"
    
    # Clean up any leftover test data
    conn = get_db_connection()
    conn.execute("DELETE FROM counterfactual_trades WHERE account_key = ?", (test_account,))
    conn.commit()
    conn.close()

    setup = {
        "symbol": test_symbol,
        "direction": test_direction,
        "pattern": test_pattern,
        "price": 83000.0,
        "stop_loss": 82500.0,
        "take_profit": 84500.0,
        "regime": "TRENDING",
        "hurst": 0.60
    }

    # 1. First registration must succeed
    res1 = tracker.register_shadow_trade(
        setup=setup,
        account_key=test_account,
        strategy_mode="TEST_SHADOW",
        rejection_reasons=["TEST_FILTER_1"]
    )
    assert res1 is True, "First shadow trade must register successfully"

    # 2. Second registration for identical open trade must be rejected by deduplication invariant
    res2 = tracker.register_shadow_trade(
        setup=setup,
        account_key=test_account,
        strategy_mode="TEST_SHADOW",
        rejection_reasons=["TEST_FILTER_2"]
    )
    assert res2 is False, "Duplicate shadow trade while OPEN must be rejected"

    # Verify only 1 row exists in the database
    conn = get_db_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM counterfactual_trades WHERE account_key = ? AND status = 'OPEN'",
        (test_account,)
    ).fetchone()[0]
    assert count == 1, f"Expected exactly 1 open trade, got {count}"

    # 3. Simulate closing the trade
    conn.execute(
        "UPDATE counterfactual_trades SET status = 'CLOSED', outcome = 'HIT_TP' WHERE account_key = ?",
        (test_account,)
    )
    conn.commit()
    conn.close()

    # 4. Third registration after close should succeed
    res3 = tracker.register_shadow_trade(
        setup=setup,
        account_key=test_account,
        strategy_mode="TEST_SHADOW",
        rejection_reasons=["TEST_FILTER_3"]
    )
    assert res3 is True, "New shadow trade after previous trade closed must succeed"

    # Cleanup
    conn = get_db_connection()
    conn.execute("DELETE FROM counterfactual_trades WHERE account_key = ?", (test_account,))
    conn.commit()
    conn.close()
