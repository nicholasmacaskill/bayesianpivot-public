"""
Realistic De-Biased Institutional Crucible
==========================================
Evaluates the Sovereign EQH/EQL Liquidity Sweep Trap under REALISTIC market conditions:
- Stop distance: 0.40% to 0.60% ($350-$500 on BTC, $16 on Gold)
- Full institutional friction: 6.5 bps (spread + slippage + taker commission)
- Pessimistic collision (SL hit first if both touched in same bar)
- Stateful Anti-Stacking (max 1 active position per asset)
- Filtered by Volume Exhaustion (CVD absorption proxy) & Active Institutional Killzones
"""

import pandas as pd
import numpy as np

def run_realistic_crucible(csv_path, asset_name, r_target=2.5, min_stop_pct=0.005, vol_filter=True, killzone_only=True):
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
    hours = df['hour_utc'].values

    # Realistic institutional friction: 6.5 bps round trip
    friction_pct = 0.00065 

    trades = []
    active_until = -1

    for i in range(60, n - 48):
        if i <= active_until:
            continue

        c_hour = hours[i]
        # Killzone filter: London (7-11) + NY (12-17)
        if killzone_only and c_hour not in [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]:
            continue

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
        c_range = max(c_high - c_low, 1e-8)
        c_vol = volumes[i]
        c_sma = vol_smas[i]

        if vol_filter and c_vol < (c_sma * 1.25):
            continue

        prior_highs = highs[i-48:i-4]
        if len(prior_highs) < 20: continue
        max_prior_high = np.max(prior_highs)
        near_peaks = prior_highs[prior_highs >= max_prior_high * 0.9992]
        is_eqh = len(near_peaks) >= 2

        prior_lows = lows[i-48:i-4]
        min_prior_low = np.min(prior_lows)
        near_troughs = prior_lows[prior_lows <= min_prior_low * 1.0008]
        is_eql = len(near_troughs) >= 2

        signal = None
        if is_eqh and c_high > max_prior_high and c_close < max_prior_high:
            if (c_high - max(c_open, c_close)) / c_range >= 0.30:
                signal = 'SHORT'
        elif is_eql and c_low < min_prior_low and c_close > min_prior_low:
            if (min(c_open, c_close) - c_low) / c_range >= 0.30:
                signal = 'LONG'

        if not signal: continue

        raw_entry = opens[i+1]
        # Realistic stop distance: at least min_stop_pct (0.50%)
        if signal == 'SHORT':
            entry_p = raw_entry * (1.0 - friction_pct)
            raw_sl = c_high * 1.0010
            stop_dist = max(raw_sl - entry_p, entry_p * min_stop_pct)
            sl_p = entry_p + stop_dist
            tp_p = entry_p - (stop_dist * r_target)
        else:
            entry_p = raw_entry * (1.0 + friction_pct)
            raw_sl = c_low * 0.9990
            stop_dist = max(entry_p - raw_sl, entry_p * min_stop_pct)
            sl_p = entry_p - stop_dist
            tp_p = entry_p + (stop_dist * r_target)

        friction_r = friction_pct * (entry_p / stop_dist)

        outcome = 'TIMEOUT'
        final_r = 0.0
        exit_idx = min(i + 48, n - 1)

        for j in range(i+1, exit_idx + 1):
            if signal == 'SHORT':
                sl_hit = highs[j] >= sl_p
                tp_hit = lows[j] <= tp_p
                if sl_hit and tp_hit: # Pessimistic collision
                    outcome = 'HIT_SL'
                    final_r = -1.0 - friction_r
                    exit_idx = j
                    break
                elif sl_hit:
                    outcome = 'HIT_SL'
                    final_r = -1.0 - friction_r
                    exit_idx = j
                    break
                elif tp_hit:
                    outcome = 'HIT_TP'
                    final_r = r_target - friction_r
                    exit_idx = j
                    break
            else:
                sl_hit = lows[j] <= sl_p
                tp_hit = highs[j] >= tp_p
                if sl_hit and tp_hit: # Pessimistic collision
                    outcome = 'HIT_SL'
                    final_r = -1.0 - friction_r
                    exit_idx = j
                    break
                elif sl_hit:
                    outcome = 'HIT_SL'
                    final_r = -1.0 - friction_r
                    exit_idx = j
                    break
                elif tp_hit:
                    outcome = 'HIT_TP'
                    final_r = r_target - friction_r
                    exit_idx = j
                    break

        if outcome == 'TIMEOUT':
            exit_p = closes[exit_idx]
            if signal == 'SHORT':
                final_r = (entry_p - exit_p) / stop_dist
            else:
                final_r = (exit_p - entry_p) / stop_dist
            final_r = max(-1.0, min(r_target, final_r)) - friction_r

        active_until = exit_idx
        trades.append({"r": final_r, "outcome": outcome})

    tdf = pd.DataFrame(trades)
    if len(tdf) == 0: return {"trades": 0, "net_r": 0, "wr": 0, "pf": 0, "dd": 0}
    wins = len(tdf[tdf['outcome'] == 'HIT_TP'])
    losses = len(tdf[tdf['outcome'] == 'HIT_SL'])
    dec = wins + losses
    wr = (wins / dec * 100) if dec > 0 else 0
    net_r = tdf['r'].sum()
    gw = tdf[tdf['r'] > 0]['r'].sum()
    gl = abs(tdf[tdf['r'] < 0]['r'].sum())
    pf = (gw / gl) if gl > 0 else 999.0
    cum = tdf['r'].cumsum()
    dd = np.max(np.maximum.accumulate(cum) - cum) if len(cum) > 0 else 0

    return {
        "asset": asset_name,
        "trades": len(tdf),
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 1),
        "net_r": round(net_r, 2),
        "pf": round(pf, 2),
        "dd": round(dd, 2)
    }

if __name__ == "__main__":
    btc_p = "./data/cache/BTC_USDT_5m_2025-01-01_2025-12-31.csv"
    gold_p = "./data/cache/PAXG_USDT_5m_2025-01-01_2025-12-31.csv"

    print("\n" + "="*80)
    print("  INSTITUTIONAL CRUCIBLE: DE-BIASED REALISTIC SIZING & FULL FRICTION")
    print("="*80)

    for min_stop in [0.003, 0.005, 0.007]:
        print(f"\n--- Testing Min Stop Floor = {min_stop*100:.2f}% (Full Friction: 6.5 bps + Pessimistic Collision) ---")
        b = run_realistic_crucible(btc_p, "BTC", r_target=2.5, min_stop_pct=min_stop)
        g = run_realistic_crucible(gold_p, "Gold", r_target=2.5, min_stop_pct=min_stop)
        comb_r = b['net_r'] + g['net_r']
        print(f"  BTC : {b['trades']} trades | WR: {b['wr']}% | Net: {b['net_r']:+7.2f}R | PF: {b['pf']:.2f} | DD: {b['dd']:.1f}R")
        print(f"  Gold: {g['trades']} trades | WR: {g['wr']}% | Net: {g['net_r']:+7.2f}R | PF: {g['pf']:.2f} | DD: {g['dd']:.1f}R")
        print(f"  COMBINED NET: {comb_r:+8.2f} R")
