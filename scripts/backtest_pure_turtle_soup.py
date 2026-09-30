"""
Pure Turtle Soup & LuxAlgo Trap Backtest (Mirroring Shadow Lab Master Weapons)
=============================================================================
Tests the exact Turtle Soup liquidity sweep rules:
- 20-candle swing high/low sweep
- Rejection wick >= 30%
- Target: 2.5R to 3.0R
- Breathing room: Hard SL held, no suffocating trailing stops
"""

import pandas as pd
import numpy as np

def load_data(filepath):
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['hour_utc'] = df['timestamp'].dt.hour
    df['range'] = df['high'] - df['low']
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    return df

def test_turtle_soup(df, asset_name="BTC/USDT", r_target=2.8):
    trades = []
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    volumes = df['volume'].values
    vol_smas = df['vol_sma20'].values
    hours = df['hour_utc'].values

    for i in range(25, n - 40):
        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
        c_range = max(c_high - c_low, 1e-8)
        c_hour = hours[i]

        prior_high = np.max(highs[i-20:i])
        prior_low = np.min(lows[i-20:i])

        # Bearish Turtle Soup (Sweep of 20-period High)
        if c_high > prior_high and c_close < prior_high:
            upper_wick = c_high - max(c_open, c_close)
            if (upper_wick / c_range) >= 0.30:
                entry = opens[i+1]
                sl = c_high * 1.0003
                risk = sl - entry
                if risk <= 0: continue
                tp = entry - (risk * r_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                for j in range(i+1, min(i + 36, n)):
                    if highs[j] >= sl:
                        outcome = 'HIT_SL'
                        final_r = -1.0
                        break
                    elif lows[j] <= tp:
                        outcome = 'HIT_TP'
                        final_r = r_target
                        break
                if outcome == 'TIMEOUT':
                    final_r = max(-1.0, (entry - closes[min(i + 36, n - 1)]) / risk)

                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({"type": "TURTLE_SOUP_BEAR", "session": session, "r": final_r, "outcome": outcome})

        # Bullish Turtle Soup (Sweep of 20-period Low)
        elif c_low < prior_low and c_close > prior_low:
            lower_wick = min(c_open, c_close) - c_low
            if (lower_wick / c_range) >= 0.30:
                entry = opens[i+1]
                sl = c_low * 0.9997
                risk = entry - sl
                if risk <= 0: continue
                tp = entry + (risk * r_target)

                outcome = 'TIMEOUT'
                final_r = 0.0
                for j in range(i+1, min(i + 36, n)):
                    if lows[j] <= sl:
                        outcome = 'HIT_SL'
                        final_r = -1.0
                        break
                    elif highs[j] >= tp:
                        outcome = 'HIT_TP'
                        final_r = r_target
                        break
                if outcome == 'TIMEOUT':
                    final_r = max(-1.0, (closes[min(i + 36, n - 1)] - entry) / risk)

                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({"type": "TURTLE_SOUP_BULL", "session": session, "r": final_r, "outcome": outcome})

    return pd.DataFrame(trades)

def print_ts_results(name, df):
    if len(df) == 0: return
    wins = len(df[df['outcome'] == 'HIT_TP'])
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
    print(f"🐢 {name.upper()}")
    print(f"=======================================================")
    print(f"  Total Trades:     {total}")
    print(f"  Win / Loss:       {wins} Wins / {losses} Losses")
    print(f"  Win Rate:         {wr:.1f}%")
    print(f"  Profit Factor:    {pf:.2f}")
    print(f"  Total Net R:      {total_r:+,.2f}R")
    print(f"  Max Drawdown:     {dd:.2f}R")

    print("\n  --- By Session ---")
    for sess, s_df in df.groupby('session'):
        s_wins = len(s_df[s_df['outcome'] == 'HIT_TP'])
        s_tot = len(s_df)
        s_wr = (s_wins / s_tot) * 100 if s_tot > 0 else 0.0
        print(f"    {sess:<8} | Trades: {s_tot:>4} | WR: {s_wr:>5.1f}% | Net R: {s_df['r'].sum():>+8.2f}R")

if __name__ == "__main__":
    print("🚀 RUNNING TURTLE SOUP LIQUIDITY SWEEP CRUCIBLE...")
    df_btc = load_data("data/cache/BTC_USDT_5m_2025-01-01_2025-12-31.csv")
    btc_ts = test_turtle_soup(df_btc, "BTC/USDT", r_target=2.8)
    print_ts_results("BTC Turtle Soup Sweep (1-Year 2025)", btc_ts)

    df_paxg = load_data("data/cache/PAXG_USDT_5m_2025-01-01_2025-12-31.csv")
    paxg_ts = test_turtle_soup(df_paxg, "PAXG/USDT", r_target=2.8)
    print_ts_results("Gold/PAXG Turtle Soup Sweep (1-Year 2025)", paxg_ts)
