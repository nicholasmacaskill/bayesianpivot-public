"""
Systematic Stress-Testing of the 1-Year Retail Trap Backtest Against the 5 Major Quant Biases
=============================================================================================
Takes the exact Equal Highs / Equal Lows (EQH/EQL) model from the dossier (+232.41R baseline)
and progressively tests the impact of:

1. Baseline (Raw script from dossier)
2. Bias 1: Pessimistic Intra-bar Collision (If both SL and TP touched in same candle, SL hit first)
3. Bias 2: Spread & Adverse Execution Slippage (2.5 bps spread + 2 bps slippage)
4. Bias 3: Broker Commission / Taker Fees (2 bps per round trip)
5. Bias 4: Anti-Stacking Stateful Execution (Only 1 active position per asset at a time)
6. Bias 5: Stop Buffer Sensitivity (Testing 0.05% vs 0.15% vs 0.30% vs 0.50% stop breathing room)
"""

import pandas as pd
import numpy as np

def run_stress_test(
    csv_path, 
    asset_name,
    pessimistic_collision=False,
    spread_slippage_bps=0.0,
    commission_bps=0.0,
    anti_stacking=False,
    stop_buffer_pct=0.0005, # 5 bps default in original script
    r_multiple_target=2.5,
    max_hold_bars=48
):
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['hour_utc'] = df['timestamp'].dt.hour
    df['range'] = df['high'] - df['low']
    df['vol_sma20'] = df['volume'].rolling(20).mean()

    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    volumes = df['volume'].values
    vol_smas = df['vol_sma20'].values
    timestamps = df['timestamp'].values
    hours = df['hour_utc'].values

    trades = []
    active_until_idx = -1

    friction_pct = (spread_slippage_bps + commission_bps) / 10000.0

    for i in range(60, n - max_hold_bars):
        if anti_stacking and i <= active_until_idx:
            continue

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
        c_range = max(c_high - c_low, 1e-8)
        c_hour = hours[i]

        prior_highs = highs[i-48:i-4]
        if len(prior_highs) < 20: continue
        max_prior_high = np.max(prior_highs)
        near_peaks = prior_highs[prior_highs >= max_prior_high * 0.9992]
        is_eqh = len(near_peaks) >= 2

        prior_lows = lows[i-48:i-4]
        min_prior_low = np.min(prior_lows)
        near_troughs = prior_lows[prior_lows <= min_prior_low * 1.0008]
        is_eql = len(near_troughs) >= 2

        # ---------------- BEARISH TRAP ----------------
        if is_eqh and c_high > max_prior_high and c_close < max_prior_high:
            upper_wick = c_high - max(c_open, c_close)
            if (upper_wick / c_range) >= 0.35:
                raw_entry = opens[i+1]
                entry_price = raw_entry * (1.0 - friction_pct) # adverse fill
                stop_loss = c_high * (1.0 + stop_buffer_pct)
                risk = stop_loss - entry_price
                if risk <= 0: continue
                tp_price = entry_price - (risk * r_multiple_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                exit_idx = min(i + max_hold_bars, n - 1)

                for j in range(i+1, exit_idx + 1):
                    sl_hit = highs[j] >= stop_loss
                    tp_hit = lows[j] <= tp_price

                    if pessimistic_collision and sl_hit and tp_hit:
                        outcome = 'HIT_SL'
                        final_r = -1.0 - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break
                    elif sl_hit:
                        outcome = 'HIT_SL'
                        final_r = -1.0 - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break
                    elif tp_hit:
                        outcome = 'HIT_TP'
                        final_r = r_multiple_target - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break

                if outcome == 'TIMEOUT':
                    exit_price = closes[exit_idx]
                    final_r = (entry_price - exit_price) / risk
                    final_r = max(-1.0, min(r_multiple_target, final_r))

                active_until_idx = exit_idx
                trades.append({"r": final_r, "outcome": outcome})

        # ---------------- BULLISH TRAP ----------------
        elif is_eql and c_low < min_prior_low and c_close > min_prior_low:
            lower_wick = min(c_open, c_close) - c_low
            if (lower_wick / c_range) >= 0.35:
                raw_entry = opens[i+1]
                entry_price = raw_entry * (1.0 + friction_pct) # adverse fill
                stop_loss = c_low * (1.0 - stop_buffer_pct)
                risk = entry_price - stop_loss
                if risk <= 0: continue
                tp_price = entry_price + (risk * r_multiple_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                exit_idx = min(i + max_hold_bars, n - 1)

                for j in range(i+1, exit_idx + 1):
                    sl_hit = lows[j] <= stop_loss
                    tp_hit = highs[j] >= tp_price

                    if pessimistic_collision and sl_hit and tp_hit:
                        outcome = 'HIT_SL'
                        final_r = -1.0 - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break
                    elif sl_hit:
                        outcome = 'HIT_SL'
                        final_r = -1.0 - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break
                    elif tp_hit:
                        outcome = 'HIT_TP'
                        final_r = r_multiple_target - (friction_pct * (entry_price / risk))
                        exit_idx = j
                        break

                if outcome == 'TIMEOUT':
                    exit_price = closes[exit_idx]
                    final_r = (exit_price - entry_price) / risk
                    final_r = max(-1.0, min(r_multiple_target, final_r))

                active_until_idx = exit_idx
                trades.append({"r": final_r, "outcome": outcome})

    tdf = pd.DataFrame(trades)
    if len(tdf) == 0:
        return {"trades": 0, "net_r": 0.0, "win_rate": 0.0, "pf": 0.0, "dd": 0.0}

    wins = len(tdf[tdf['outcome'] == 'HIT_TP'])
    losses = len(tdf[tdf['outcome'] == 'HIT_SL'])
    decided = wins + losses
    wr = (wins / decided * 100) if decided > 0 else 0.0
    net_r = tdf['r'].sum()
    gw = tdf[tdf['r'] > 0]['r'].sum()
    gl = abs(tdf[tdf['r'] < 0]['r'].sum())
    pf = (gw / gl) if gl > 0 else 999.0

    cum = tdf['r'].cumsum()
    dd = np.max(np.maximum.accumulate(cum) - cum) if len(cum) > 0 else 0.0

    return {
        "trades": len(tdf),
        "decided": decided,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "net_r": round(net_r, 2),
        "pf": round(pf, 2),
        "dd": round(dd, 2)
    }

def run_all_stress_tests():
    btc_p = "./data/cache/BTC_USDT_5m_2025-01-01_2025-12-31.csv"
    gold_p = "./data/cache/PAXG_USDT_5m_2025-01-01_2025-12-31.csv"

    scenarios = [
        ("1. Baseline (Raw Script from Dossier)", {}),
        ("2. + Pessimistic Collision (SL hit first if both touched)", {"pessimistic_collision": True}),
        ("3. + Spread & Slippage (4.5 bps total friction)", {"pessimistic_collision": True, "spread_slippage_bps": 4.5}),
        ("4. + Taker Commission (2.0 bps)", {"pessimistic_collision": True, "spread_slippage_bps": 4.5, "commission_bps": 2.0}),
        ("5. + Anti-Stacking (Single Active Position)", {"pessimistic_collision": True, "spread_slippage_bps": 4.5, "commission_bps": 2.0, "anti_stacking": True}),
        ("6. + Real World Buffer (0.15% stop buffer)", {"pessimistic_collision": True, "spread_slippage_bps": 4.5, "commission_bps": 2.0, "anti_stacking": True, "stop_buffer_pct": 0.0015}),
        ("7. + Wide Anti-Suffocation Buffer (0.30% buffer)", {"pessimistic_collision": True, "spread_slippage_bps": 4.5, "commission_bps": 2.0, "anti_stacking": True, "stop_buffer_pct": 0.0030}),
    ]

    print("\n" + "="*80)
    print("      DE-BIASED STRESS-TEST MATRIX (FULL-YEAR 2025: BTC + GOLD)")
    print("="*80)
    print(f"{'Scenario':<42} | {'Trades':<6} | {'BTC R':<8} | {'Gold R':<8} | {'Combined R':<10} | {'Combined PF':<6} | {'Max DD':<7}")
    print("-" * 96)

    for label, kwargs in scenarios:
        b_res = run_stress_test(btc_p, "BTC", **kwargs)
        g_res = run_stress_test(gold_p, "Gold", **kwargs)
        comb_r = b_res['net_r'] + g_res['net_r']
        tot_trades = b_res['trades'] + g_res['trades']
        tot_gw = (b_res['wins'] * 2.5) + (g_res['wins'] * 2.5)
        tot_gl = b_res['losses'] + g_res['losses']
        comb_pf = round(tot_gw / max(tot_gl, 1), 2)
        comb_dd = round(max(b_res['dd'], g_res['dd']), 2)

        print(f"{label:<42} | {tot_trades:<6d} | {b_res['net_r']:+7.1f}R | {g_res['net_r']:+7.1f}R | {comb_r:+9.2f} R | {comb_pf:<6.2f} | {comb_dd:<7.1f}R")

if __name__ == "__main__":
    run_all_stress_tests()
