"""
Rapid Backtest: Common Retail Traps & Inducement Fades
======================================================
Backtests 1 Full Year (104,835 5m candles) across BTC and Gold/PAXG.

Models Tested:
1. Equal Highs / Equal Lows (EQH/EQL) Liquidity Sweep Trap (Double Top/Bottom fakeout)
2. Tokyo / Asian Range Box Breakout Trap (Judas Inducement)
3. Volume Exhaustion Confluence (CVD Proxy: Volume > 1.5x SMA with Rejection Wick)
"""

import pandas as pd
import numpy as np
from datetime import datetime

def load_data(filepath):
    print(f"Loading {filepath}...")
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['hour_utc'] = df['timestamp'].dt.hour
    df['range'] = df['high'] - df['low']
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    return df

def backtest_eqh_eql_traps(df, asset_name="BTC/USDT", r_multiple_target=2.5):
    """
    Backtests Equal Highs / Equal Lows Sweeps.
    """
    trades = []
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    volumes = df['volume'].values
    vol_smas = df['vol_sma20'].values
    timestamps = df['timestamp'].values
    hours = df['hour_utc'].values

    # Lookback window for equal levels: 15 to 60 candles (1.25 to 5 hours)
    for i in range(60, n - 50):
        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
        c_range = max(c_high - c_low, 1e-8)
        c_hour = hours[i]
        
        # 1. Look for prior swing highs
        prior_highs = highs[i-48:i-4]
        if len(prior_highs) < 20:
            continue
            
        max_prior_high = np.max(prior_highs)
        # Check if there was an EQH (two peaks within 0.08% of each other)
        near_peaks = prior_highs[prior_highs >= max_prior_high * 0.9992]
        is_eqh = len(near_peaks) >= 2

        # -------------------------------------------------------------
        # BEARISH TRAP: Price sweeps EQH, then closes back below
        # -------------------------------------------------------------
        if is_eqh and c_high > max_prior_high and c_close < max_prior_high:
            upper_wick = c_high - max(c_open, c_close)
            wick_ratio = upper_wick / c_range
            
            if wick_ratio >= 0.35: # Strong rejection wick
                entry_price = opens[i+1]
                stop_loss = c_high * 1.0005 # 5 bps above sweep
                risk = stop_loss - entry_price
                if risk <= 0: continue
                
                tp_price = entry_price - (risk * r_multiple_target)
                has_vol_exhaustion = volumes[i] > (vol_smas[i] * 1.3)
                
                # Forward simulate trade
                outcome = 'TIMEOUT'
                final_r = 0.0
                for j in range(i+1, min(i + 48, n)): # Max hold 4 hours
                    if highs[j] >= stop_loss:
                        outcome = 'HIT_SL'
                        final_r = -1.0
                        break
                    elif lows[j] <= tp_price:
                        outcome = 'HIT_TP'
                        final_r = r_multiple_target
                        break
                
                if outcome == 'TIMEOUT':
                    exit_price = closes[min(i + 48, n - 1)]
                    final_r = (entry_price - exit_price) / risk
                
                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({
                    "type": "EQH_BEAR_TRAP",
                    "timestamp": timestamps[i],
                    "session": session,
                    "hour": c_hour,
                    "r": final_r,
                    "outcome": outcome,
                    "vol_exhaustion": has_vol_exhaustion
                })

        # -------------------------------------------------------------
        # BULLISH TRAP: Price sweeps EQL, then closes back above
        # -------------------------------------------------------------
        prior_lows = lows[i-48:i-4]
        min_prior_low = np.min(prior_lows)
        near_troughs = prior_lows[prior_lows <= min_prior_low * 1.0008]
        is_eql = len(near_troughs) >= 2

        if is_eql and c_low < min_prior_low and c_close > min_prior_low:
            lower_wick = min(c_open, c_close) - c_low
            wick_ratio = lower_wick / c_range
            
            if wick_ratio >= 0.35:
                entry_price = opens[i+1]
                stop_loss = c_low * 0.9995
                risk = entry_price - stop_loss
                if risk <= 0: continue
                
                tp_price = entry_price + (risk * r_multiple_target)
                has_vol_exhaustion = volumes[i] > (vol_smas[i] * 1.3)
                
                outcome = 'TIMEOUT'
                final_r = 0.0
                for j in range(i+1, min(i + 48, n)):
                    if lows[j] <= stop_loss:
                        outcome = 'HIT_SL'
                        final_r = -1.0
                        break
                    elif highs[j] >= tp_price:
                        outcome = 'HIT_TP'
                        final_r = r_multiple_target
                        break
                
                if outcome == 'TIMEOUT':
                    exit_price = closes[min(i + 48, n - 1)]
                    final_r = (exit_price - entry_price) / risk
                
                session = "ASIAN" if 0 <= c_hour < 6 else ("LONDON" if 7 <= c_hour < 12 else ("NY" if 12 <= c_hour < 18 else "OTHER"))
                trades.append({
                    "type": "EQL_BULL_TRAP",
                    "timestamp": timestamps[i],
                    "session": session,
                    "hour": c_hour,
                    "r": final_r,
                    "outcome": outcome,
                    "vol_exhaustion": has_vol_exhaustion
                })

    return pd.DataFrame(trades)

def backtest_asian_box_traps(df, asset_name="BTC/USDT", r_multiple_target=2.0):
    """
    Backtests Asian Initial Box Breakout Traps (Tokyo Judas Inducement).
    Box formed: 00:00 to 02:00 UTC.
    Breakout Window: 02:00 to 06:00 UTC.
    """
    trades = []
    df = df.copy()
    df['date'] = df['timestamp'].dt.date
    
    for date, day_df in df.groupby('date'):
        if len(day_df) < 50: continue
        
        box_data = day_df[(day_df['hour_utc'] >= 0) & (day_df['hour_utc'] < 2)]
        if len(box_data) < 12: continue
        
        box_high = box_data['high'].max()
        box_low = box_data['low'].min()
        box_height = box_high - box_low
        if box_height <= 0: continue
        
        trade_window = day_df[(day_df['hour_utc'] >= 2) & (day_df['hour_utc'] < 6)]
        if len(trade_window) == 0: continue
        
        traded_today = False
        for idx, row in trade_window.iterrows():
            if traded_today: break
            
            c_high, c_low, c_open, c_close = row['high'], row['low'], row['open'], row['close']
            c_range = max(c_high - c_low, 1e-8)
            
            # False Upside Breakout Trap (Judas Short)
            if c_high > box_high and c_close < box_high:
                upper_wick = c_high - max(c_open, c_close)
                if (upper_wick / c_range) >= 0.30:
                    entry = c_close
                    sl = c_high * 1.0005
                    risk = sl - entry
                    if risk <= 0: continue
                    tp = entry - (risk * r_multiple_target)
                    
                    # Forward check until 10:00 UTC (London open)
                    fwd = day_df[(day_df['timestamp'] > row['timestamp']) & (day_df['hour_utc'] < 10)]
                    outcome = 'TIMEOUT'
                    final_r = 0.0
                    for _, f_row in fwd.iterrows():
                        if f_row['high'] >= sl:
                            outcome = 'HIT_SL'
                            final_r = -1.0
                            break
                        elif f_row['low'] <= tp:
                            outcome = 'HIT_TP'
                            final_r = r_multiple_target
                            break
                    if outcome == 'TIMEOUT' and len(fwd) > 0:
                        final_r = (entry - fwd.iloc[-1]['close']) / risk
                    
                    trades.append({
                        "type": "ASIAN_BOX_FALSE_BREAKOUT_SHORT",
                        "timestamp": row['timestamp'],
                        "session": "ASIAN",
                        "r": final_r,
                        "outcome": outcome
                    })
                    traded_today = True

            # False Downside Breakdown Trap (Judas Long)
            elif c_low < box_low and c_close > box_low:
                lower_wick = min(c_open, c_close) - c_low
                if (lower_wick / c_range) >= 0.30:
                    entry = c_close
                    sl = c_low * 0.9995
                    risk = entry - sl
                    if risk <= 0: continue
                    tp = entry + (risk * r_multiple_target)
                    
                    fwd = day_df[(day_df['timestamp'] > row['timestamp']) & (day_df['hour_utc'] < 10)]
                    outcome = 'TIMEOUT'
                    final_r = 0.0
                    for _, f_row in fwd.iterrows():
                        if f_row['low'] <= sl:
                            outcome = 'HIT_SL'
                            final_r = -1.0
                            break
                        elif f_row['high'] >= tp:
                            outcome = 'HIT_TP'
                            final_r = r_multiple_target
                            break
                    if outcome == 'TIMEOUT' and len(fwd) > 0:
                        final_r = (fwd.iloc[-1]['close'] - entry) / risk
                    
                    trades.append({
                        "type": "ASIAN_BOX_FALSE_BREAKOUT_LONG",
                        "timestamp": row['timestamp'],
                        "session": "ASIAN",
                        "r": final_r,
                        "outcome": outcome
                    })
                    traded_today = True

    return pd.DataFrame(trades)

def print_metrics(name, df_trades):
    if len(df_trades) == 0:
        print(f"\n[{name}] No trades generated.")
        return
        
    wins = len(df_trades[df_trades['r'] > 0])
    losses = len(df_trades[df_trades['r'] <= 0])
    total = len(df_trades)
    wr = (wins / total) * 100 if total > 0 else 0.0
    total_r = df_trades['r'].sum()
    avg_r = df_trades['r'].mean()
    
    # Cumulative Drawdown
    equity_curve = df_trades['r'].cumsum()
    running_max = equity_curve.cummax()
    dd = running_max - equity_curve
    max_dd = dd.max()
    
    gross_win = df_trades[df_trades['r'] > 0]['r'].sum()
    gross_loss = abs(df_trades[df_trades['r'] <= 0]['r'].sum())
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    
    print(f"\n=======================================================")
    print(f"📊 {name.upper()}")
    print(f"=======================================================")
    print(f"  Total Trades:     {total}")
    print(f"  Win / Loss:       {wins} Wins / {losses} Losses")
    print(f"  Win Rate:         {wr:.1f}%")
    print(f"  Profit Factor:    {profit_factor:.2f}")
    print(f"  Total Net R:      {total_r:+,.2f}R")
    print(f"  Avg Trade R:      {avg_r:+,.2f}R")
    print(f"  Max Drawdown:     {max_dd:.2f}R")
    
    # Session breakdown if available
    if 'session' in df_trades.columns:
        print("\n  --- By Session ---")
        for sess, s_df in df_trades.groupby('session'):
            s_wins = len(s_df[s_df['r'] > 0])
            s_tot = len(s_df)
            s_wr = (s_wins / s_tot) * 100 if s_tot > 0 else 0.0
            print(f"    {sess:<8} | Trades: {s_tot:>4} | WR: {s_wr:>5.1f}% | Net R: {s_df['r'].sum():>+8.2f}R")

    # Volume Exhaustion Filter comparison if available
    if 'vol_exhaustion' in df_trades.columns:
        vol_df = df_trades[df_trades['vol_exhaustion'] == True]
        v_wins = len(vol_df[vol_df['r'] > 0])
        v_tot = len(vol_df)
        v_wr = (v_wins / v_tot) * 100 if v_tot > 0 else 0.0
        print(f"\n  --- With Volume Exhaustion Confluence (CVD Proxy) ---")
        print(f"    Trades: {v_tot} | Win Rate: {v_wr:.1f}% | Net R: {vol_df['r'].sum():+,.2f}R | PF: {(vol_df[vol_df['r'] > 0]['r'].sum() / abs(vol_df[vol_df['r'] <= 0]['r'].sum())):.2f}")

if __name__ == "__main__":
    print("🚀 STARTING 1-YEAR RETAIL TRAP HISTORICAL CRUCIBLE...")
    
    # 1. BTC/USDT (1 Full Year 2025)
    df_btc = load_data("data/cache/BTC_USDT_5m_2025-01-01_2025-12-31.csv")
    
    btc_eqh = backtest_eqh_eql_traps(df_btc, "BTC/USDT", r_multiple_target=2.5)
    print_metrics("BTC Equal Highs/Lows Sweep Traps (1-Year)", btc_eqh)
    
    btc_box = backtest_asian_box_traps(df_btc, "BTC/USDT", r_multiple_target=2.0)
    print_metrics("BTC Asian Tokyo Box Breakout Traps (1-Year)", btc_box)

    # 2. Gold/PAXG (1 Full Year 2025)
    df_paxg = load_data("data/cache/PAXG_USDT_5m_2025-01-01_2025-12-31.csv")
    
    paxg_eqh = backtest_eqh_eql_traps(df_paxg, "PAXG/USDT", r_multiple_target=2.5)
    print_metrics("Gold/PAXG Equal Highs/Lows Sweep Traps (1-Year)", paxg_eqh)
    
    paxg_box = backtest_asian_box_traps(df_paxg, "PAXG/USDT", r_multiple_target=2.0)
    print_metrics("Gold/PAXG Asian Tokyo Box Breakout Traps (1-Year)", paxg_box)
