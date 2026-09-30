import sys
import os
sys.path.insert(0, os.getcwd())

from src.engines.execution_shadow_engine import ExecutionShadowEngine

def print_leaderboard():
    data = ExecutionShadowEngine.get_leaderboard()
    print("\n" + "="*70)
    print("⚔️  EXECUTION STRATEGY SHADOW TOURNAMENT (A/B FORWARD EXPERIMENT)")
    print("="*70)
    
    total = data.get("total_trades", 0)
    print(f"Total Completed Trades Analyzed: {total}\n")
    
    if total == 0:
        print("No completed execution tournament trades yet. Live tracking in progress...")
        print("="*70 + "\n")
        return

    live = data.get("live_ratchet", {})
    shadow = data.get("shadow_partial", {})
    binary = data.get("shadow_binary", {})
    variant = data.get("shadow_variant_sizing", {})
    buffered = data.get("shadow_buffered", {})
    session_adapt = data.get("shadow_session_adaptive", {})
    edge = data.get("alpha_edge_usd", 0.0)

    print(f"{'Strategy Variant':<30} | {'Win Rate':<10} | {'Total R':<10} | {'Net PnL ($)':<12}")
    print("-"*70)
    print(f"{'1. LIVE FLAT 1.0x (Prod)':<30} | {live.get('win_rate', 0.0):>8.1f}% | {live.get('total_r', 0.0):>+8.2f}R | ${live.get('total_pnl', 0.0):>+10.2f}")
    print(f"{'2. SHADOW VARIANT SIZING':<30} | {live.get('win_rate', 0.0):>8.1f}% | {live.get('total_r', 0.0):>+8.2f}R | ${variant.get('total_pnl', 0.0):>+10.2f}")
    print(f"{'3. SHADOW PARTIAL SCALE-OUT':<30} | {shadow.get('win_rate', 0.0):>8.1f}% | {shadow.get('total_r', 0.0):>+8.2f}R | ${shadow.get('total_pnl', 0.0):>+10.2f}")
    print(f"{'4. SHADOW PURE BINARY':<30} | {binary.get('win_rate', 0.0):>8.1f}% | {binary.get('total_r', 0.0):>+8.2f}R | ${binary.get('total_pnl', 0.0):>+10.2f}")
    print(f"{'5. SHADOW BUFFERED STOP':<30} | {buffered.get('win_rate', 0.0):>8.1f}% | {buffered.get('total_r', 0.0):>+8.2f}R | ${buffered.get('total_pnl', 0.0):>+10.2f}")
    print(f"{'6. SHADOW SESSION ADAPTIVE':<30} | {live.get('win_rate', 0.0):>8.1f}% | {live.get('total_r', 0.0):>+8.2f}R | ${session_adapt.get('total_pnl', 0.0):>+10.2f}")
    print("-"*70)
    
    variant_edge = variant.get("edge_vs_flat_usd", 0.0)
    if variant_edge >= 0:
        print(f"📈 Sizing Tournament: Shadow Variant Sizing is +${variant_edge:,.2f} vs Flat Live Sizing")
    else:
        print(f"🛡️ Sizing Tournament: Flat Live Sizing is OUTPERFORMING Variant Sizing by +${abs(variant_edge):,.2f} (Drawdown Avoided)")

    buffered_edge = buffered.get("edge_vs_live_usd", 0.0)
    if buffered_edge >= 0:
        print(f"🔬 Stop-Loss Dynamics: Shadow Buffered Stop is OUTPERFORMING Live Stop by +${buffered_edge:,.2f}")
    else:
        print(f"🛡️ Stop-Loss Dynamics: Tight Live Stop is SAVING CAPITAL by +${abs(buffered_edge):,.2f} vs Buffered Stop")

    sess_edge = session_adapt.get("edge_vs_live_usd", 0.0)
    if sess_edge >= 0:
        print(f"📈 Session Loss Budget: Session Adapted Sizing added +${sess_edge:,.2f} edge")
    else:
        print(f"🛡️ Session Loss Budget: Session Loss Ceiling preserved +${abs(sess_edge):,.2f} in risk mitigation")

    if edge >= 0:
        print(f"🏆 Exit Model Edge: Live Trailing Ratchet is OUTPERFORMING Partial Scale-Out by +${edge:,.2f}")
    else:
        print(f"💡 Exit Model Edge: Partial Scale-Out is OUTPERFORMING Live Trailing Ratchet by +${abs(edge):,.2f}")
    print("="*70 + "\n")

if __name__ == "__main__":
    print_leaderboard()
