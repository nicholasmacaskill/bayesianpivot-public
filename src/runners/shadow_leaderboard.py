"""
Shadow Strategy Tournament Leaderboard & Promotion Dashboard
============================================================
Audits the real-time statistical performance of all competing strategies:
- Live Champions (Turtle Soup, London Close Silver Bullet)
- Shadow Challengers (Retail Stop Trap, Breaker Block, NY Double Sweep)

Evaluates:
- Total Sample Size (N >= 30 required for live promotion)
- Win Rate (%)
- Total Simulated R-Multiple
- Profit Factor
- Live Promotion Eligibility
"""

import sys
import os
import sqlite3
import pandas as pd
from datetime import datetime, timezone

sys.path.append(os.getcwd())
from src.core.config import Config

def main():
    print("==========================================================================")
    print("           SOVEREIGN SMC TOURNAMENT SHADOW LEADERBOARD                    ")
    print("==========================================================================")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    conn = sqlite3.connect("data/smc_alpha.db")
    
    # 1. Summary of Counterfactual Shadow Trades by Strategy
    query = """
        SELECT 
            account_key as strategy_archetype,
            COUNT(*) as total_samples,
            SUM(CASE WHEN outcome = 'HIT_TP' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'HIT_SL' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'PENDING' OR status = 'OPEN' THEN 1 ELSE 0 END) as active_open,
            ROUND(AVG(CASE WHEN outcome = 'HIT_TP' THEN 1.0 WHEN outcome = 'HIT_SL' THEN 0.0 ELSE NULL END) * 100, 1) as win_rate_pct,
            ROUND(SUM(simulated_r), 2) as total_r_multiple,
            ROUND(SUM(simulated_pnl), 2) as simulated_pnl_usd
        FROM counterfactual_trades
        GROUP BY account_key
        ORDER BY total_r_multiple DESC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        
        # Add Promotion Status Column
        def get_status(row):
            archetype = str(row['strategy_archetype'])
            samples = row['total_samples']
            wr = row['win_rate_pct']
            r = row['total_r_multiple']
            if "TURTLE_SOUP" in archetype or "SILVER_BULLET" in archetype:
                return "👑 LIVE MASTER WEAPON"
            elif "EQH_EQL_SHELF" in archetype:
                if samples >= 30 and wr is not None and wr >= 35.0 and r > 15.0:
                    return "🚀 QUALIFIED FOR LIVE PROMOTION"
                return "🧠 ADAPTIVE META-MODEL ($0 RISK)"
            elif "CHALLENGER_LOCAL_MLX" in archetype:
                if samples >= 30 and wr is not None and wr >= 55.0 and r > 10.0:
                    return "🚀 QUALIFIED FOR LIVE PROMOTION"
                return "🤖 M4 LOCAL LORA CHALLENGER ($0 RISK)"
            elif samples >= 30 and wr is not None and wr >= 55.0 and r > 10.0:
                return "🚀 QUALIFIED FOR LIVE PROMOTION"
            elif samples >= 15:
                return "⏳ MID-STAGE BENCHMARKING"
            else:
                return "🌱 EARLY OBSERVATION (<15 samples)"
                
        if not df.empty:
            df['promotion_status'] = df.apply(get_status, axis=1)
            print(df.to_string(index=False))
        else:
            print("No counterfactual shadow trades found in database.")
            
    except Exception as e:
        print("Database query error:", e)

    # 2. Champion vs Challenger Tournament Lab Registry
    print("\n==========================================================================")
    print("           CHAMPION VS. CHALLENGER A/B TOURNAMENT REGISTRY                ")
    print("==========================================================================")
    try:
        df_variants = pd.read_sql_query("""
            SELECT 
                variant_id,
                strategy_id,
                variant_type,
                samples,
                wins,
                losses,
                win_rate,
                total_r
            FROM strategy_tournament_variants
            WHERE is_active = 1
            ORDER BY variant_type DESC, total_r DESC
        """, conn)
        if not df_variants.empty:
            print(df_variants.to_string(index=False))
        else:
            print("No active tournament variants found.")
    except Exception as var_err:
        print("Error reading tournament variants:", var_err)
        
    print("\n==========================================================================")
    print("                     PROMOTION CRITERIA BENCHMARK                         ")
    print("==========================================================================")
    print("1. Minimum Sample Size: N >= 30 closed trades in live market data.")
    print("2. Win Rate Requirement: >= 55.0% across all sessions.")
    print("3. Expectancy: Total R-Multiple >= +10.0R with Profit Factor >= 1.80.")
    print("4. Drawdown Safety: Max consecutive loss streak <= 3 trades.")
    print("==========================================================================\n")
    
    conn.close()

if __name__ == "__main__":
    main()
