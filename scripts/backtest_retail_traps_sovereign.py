"""
Rapid Backtest V3: Production Sovereign SMC Defense (Stepped Defense at +1.0R, TP at 1.8R)
=======================================================================================
Directly mirrors the production Sovereign SMC architecture:
- Target: 1.8R (Targeting Point of Control / Value Area Mean Reversion)
- Stepped Defense: At +1.0R, stop loss tightens to -0.30R (gives breathing room, avoids premature BE clipping)
- Full BE Lock: Only at +1.4R
"""

import pandas as pd
import numpy as np

def load_data(filepath):
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['hour_utc'] = df['timestamp'].dt.hour
    df['range'] = df['high'] - df['low']
    df['sma50'] = df['close'].rolling(50).mean()
    df['std50'] = df['close'].rolling(50).std()
    df['atr14'] = (df['high'] - df['low']).rolling(14).mean()
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    return df

def backtest_sovereign_traps(df, asset_name="BTC/USDT", tp_target=1.8):
    trades = []
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    atr14 = df['atr14'].values
    volumes = df['volume'].values
    vol_smas = df['vol_sma20'].values
    timestamps = df['timestamp'].values
    hours = df['hour_utc'].values

    for i in range(60, n - 50):
        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
        c_range = max(c_high - c_low, 1e-8)
        c_hour = hours[i]
        c_atr = atr14[i] if not np.isnan(atr14[i]) else 10.0
        
        # Lookback for swing highs (last 4 to 48 candles)
        prior_highs = highs[i-48:i-4]
        if len(prior_highs) < 20: continue
        max_prior_high = np.max(prior_highs)
        near_peaks = prior_highs[prior_highs >= max_prior_high * 0.9992]
        is_eqh = len(near_peaks) >= 2

        # BEARISH SWEEP TRAP
        if is_eqh and c_high > max_prior_high and c_close < max_prior_high:
            upper_wick = c_high - max(c_open, c_close)
            if (upper_wick / c_range) >= 0.35 and volumes[i] >= vol_smas[i] * 1.1:
                entry_price = opens[i+1]
                stop_loss = c_high + (c_atr * 0.10)
                risk = stop_loss - entry_price
                if risk <= 0: continue
                tp_price = entry_price - (risk * tp_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                stepped_def = False
                be_active = False

                for j in range(i+1, min(i + 36, n)):
                    # Stepped defense at +1.0R -> move stop to -0.30R
                    fwd_mfe = entry_price - lows[j]
                    if fwd_mfe >= (risk * 1.0) and not stepped_def:
                        stop_loss = entry_price + (risk * 0.30) # tighten to -0.3R
                        stepped_def = True

                    if fwd_mfe >= (risk * 1.4) and not be_active:
                        stop_loss = entry_price # Breakeven
                        be_active = True

                    if highs[j] >= stop_loss:
                        if be_active:
                            outcome = 'HIT_BE'
                            final_r = 0.0
                        elif stepped_def:
                            outcome = 'HIT_STEPPED'
                            final_r = -0.30
                        else:
                            outcome = 'HIT_SL'
                            final_r = -1.0
                        break
                    elif lows[j] <= tp_price:
                        outcome = 'HIT_TP'
                        final_r = tp_target
                        break

                if outcome == 'TIMEOUT':
                    exit_price = closes[min(i + 36, n - 1)]
                    final_r = max(-1.0, (entry_price - exit_price) / risk)

                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({
                    "type": "BEAR_SWEEP_TRAP",
                    "timestamp": timestamps[i],
                    "session": session,
                    "hour": c_hour,
                    "r": final_r,
                    "outcome": outcome
                })

        # BULLISH SWEEP TRAP
        prior_lows = lows[i-48:i-4]
        min_prior_low = np.min(prior_lows)
        near_troughs = prior_lows[prior_lows <= min_prior_low * 1.0008]
        is_eql = len(near_troughs) >= 2

        if is_eql and c_low < min_prior_low and c_close > min_prior_low:
            lower_wick = min(c_open, c_close) - c_low
            if (lower_wick / c_range) >= 0.35 and volumes[i] >= vol_smas[i] * 1.1:
                entry_price = opens[i+1]
                stop_loss = c_low - (c_atr * 0.10)
                risk = entry_price - stop_loss
                if risk <= 0: continue
                tp_price = entry_price + (risk * tp_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                stepped_def = False
                be_active = False

                for j in range(i+1, min(i + 36, n)):
                    fwd_mfe = highs[j] - entry_price
                    if fwd_mfe >= (risk * 1.0) and not stepped_def:
                        stop_loss = entry_price - (risk * 0.30)
                        stepped_def = True

                    if fwd_mfe >= (risk * 1.4) and not be_active:
                        stop_loss = entry_price
                        be_active = True

                    if lows[j] <= stop_loss:
                        if be_active:
                            outcome = 'HIT_BE'
                            final_r = 0.0
                        elif stepped_def:
                            outcome = 'HIT_STEPPED'
                            final_r = -0.30
                        else:
                            outcome = 'HIT_SL'
                            final_r = -1.0
                        break
                    elif highs[j] >= tp_price:
                        outcome = 'HIT_TP'
                        final_r = tp_target
                        break

                if outcome == 'TIMEOUT':
                    exit_price = closes[min(i + 36, n - 1)]
                    final_r = max(-1.0, (exit_price - entry_price) / risk)

                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({
                    "type": "BULL_SWEEP_TRAP",
                    "timestamp": timestamps[i],
                    "session": session,
                    "hour": c_hour,
                    "r": final_r,
                    "outcome": outcome
                })

    return pd.DataFrame(trades)

def print_sovereign_results(name, df):
    if len(df) == 0: return
    wins = len(df[df['r'] > 0])
    stepped = len(df[df['outcome'] == 'HIT_STEPPED'])
    bes = len(df[df['outcome'] == 'HIT_BE'])
    losses = len(df[df['outcome'] == 'HIT_SL'])
    total = len(df)
    wr = (wins / total) * 100
    total_r = df['r'].sum()
    
    equity_curve = df['r'].cumsum()
    running_max = equity_curve.cummax()
    dd = (running_max - equity_curve).max()
    
    gw = df[df['r'] > 0]['r'].sum()
    gl = abs(df[df['r'] < 0]['r'].sum())
    pf = gw / gl if gl > 0 else 999.0

    print(f"\n=======================================================")
    print(f"🏆 {name.upper()}")
    print(f"=======================================================")
    print(f"  Total Trades:     {total}")
    print(f"  Full TP Wins:     {wins} ({wr:.1f}%)")
    print(f"  Stepped (-0.3R):  {stepped} (Safely mitigated losses)")
    print(f"  Breakeven (0.0R): {bes}")
    print(f"  Full Losses:      {losses}")
    print(f"  Profit Factor:    {pf:.2f}")
    print(f"  Total Net R:      {total_r:+,.2f}R")
    print(f"  Max Drawdown:     {dd:.2f}R")

    print("\n  --- Performance by Session ---")
    for sess, s_df in df.groupby('session'):
        s_wins = len(s_df[s_df['r'] > 0])
        s_tot = len(s_df)
        s_wr = (s_wins / s_tot) * 100 if s_tot > 0 else 0.0
        print(f"    {sess:<8} | Trades: {s_tot:>4} | WR: {s_wr:>5.1f}% | Net R: {s_df['r'].sum():>+8.2f}R")

if __name__ == "__main__":
    print("🚀 RUNNING SOVEREIGN 1.8R STEPPED DEFENSE BACKTEST...")
    df_btc = load_data("data/cache/BTC_USDT_5m_2025-01-01_2025-12-31.csv")
    btc_res = backtest_sovereign_traps(df_btc, "BTC/USDT", tp_target=1.8)
    print_sovereign_results("BTC Sovereign Stepped Trap (1-Year 2025)", btc_res)

    df_paxg = load_data("data/cache/PAXG_USDT_5m_2025-01-01_2025-12-31.csv")
    paxg_res = backtest_sovereign_traps(df_paxg, "PAXG/USDT", tp_target=1.8)
    print_sovereign_results("Gold/PAXG Sovereign Stepped Trap (1-Year 2025)", paxg_res)
