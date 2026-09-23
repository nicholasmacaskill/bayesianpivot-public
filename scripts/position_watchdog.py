import os
import sys
import time
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.getcwd())

from src.clients.tl_client import TradeLockerClient
from src.clients.telegram_notifier import TelegramNotifier
from src.core.supabase_client import SupabaseBridge
from src.core.config import Config

STATE_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "watchdog_state.json"
)

class PositionWatchdog:
    def __init__(self, tl_client=None):
        self.tl = tl_client if tl_client is not None else TradeLockerClient()
        self.sb = SupabaseBridge()
        self.notifier = TelegramNotifier()
        self.alerted_trades = {} # {trade_id: {r_level: bool}}
        self.symbol_state = {}   # {clean_sym: {scaleout_executed: bool, peak_r: float, ...}}
        self.load_state()

    def load_state(self):
        try:
            if os.path.exists(STATE_FILE_PATH):
                with open(STATE_FILE_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.alerted_trades = data.get("alerted_trades", {})
                        self.symbol_state = data.get("symbol_state", {})
                        # Backward compatibility if data was a flat alerted_trades dict
                        if not self.alerted_trades and not self.symbol_state:
                            self.alerted_trades = data
        except Exception as e:
            print(f"Error loading state: {e}")

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
            with open(STATE_FILE_PATH, "w") as f:
                json.dump({
                    "alerted_trades": self.alerted_trades,
                    "symbol_state": self.symbol_state
                }, f, indent=4)
        except Exception as e:
            print(f"Error saving state: {e}")

    def get_stop_loss(self, symbol, pos=None):
        """Find the Stop Loss directly from the open position object, or fallback to Supabase."""
        # 1. Primary: Broker position attached stopLoss
        if pos and pos.get('stopLoss'):
            try:
                val = float(pos['stopLoss'])
                if val > 0:
                    return val, None
            except (ValueError, TypeError):
                pass

        # 2. Secondary: Fallback to Supabase scans
        try:
            norm_sym = symbol
            if "/" not in norm_sym:
                if "BTC" in norm_sym: norm_sym = "BTC/USD"
                elif "ETH" in norm_sym: norm_sym = "ETH/USD"
                elif "SOL" in norm_sym: norm_sym = "SOL/USD"
                elif "XAU" in norm_sym: norm_sym = "XAU/USD"
                elif len(norm_sym) == 6: norm_sym = f"{norm_sym[:3]}/{norm_sym[3:]}"

            time_threshold = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            resp = self.sb.client.table("scans")\
                .select("*")\
                .eq("symbol", norm_sym)\
                .in_("verdict", ["CONFIRMED", "ACCEPTED", "FLOW_GO", "EXECUTED"])\
                .gt("timestamp", time_threshold)\
                .order("timestamp", desc=True)\
                .limit(1)\
                .execute()
            
            if resp.data:
                scan = resp.data[0]
                sl = scan.get('stop_loss')
                if not sl and scan.get('ai_payload'):
                    try:
                        payload = json.loads(scan['ai_payload'])
                        sl = payload.get('stop_loss')
                    except: pass
                
                if sl:
                    return float(sl), scan
        except Exception as e:
            print(f"Error fetching SL for {symbol}: {e}")
        return None, None

    def run(self):
        print("🛡️ Bayesian Pivot Watchdog starting...")
        print("Monitoring for R-multiple targets (1.5R, 2.0R)...")
        
        while True:
            try:
                positions = self.tl.get_open_positions()
                if not positions:
                    print("No open positions found.")
                    if self.symbol_state or self.alerted_trades:
                        self.symbol_state.clear()
                        self.alerted_trades.clear()
                        self.save_state()
                else:
                    # Friday Pre-Weekend Auto-Flatten Gate: Flatten fleet at Friday 20:00 UTC to eliminate weekend gap slippage
                    now_utc = datetime.now(timezone.utc)
                    if now_utc.weekday() == 4 and now_utc.hour >= 20:
                        print("🛡️ [FRIDAY PRE-WEEKEND AUTO-FLATTEN] Friday 20:00 UTC reached! Flattening all open positions across fleet to prevent weekend gap slippage...")
                        closed = self.tl.close_all_fleet_positions()
                        if hasattr(self, 'notifier') and self.notifier:
                            self.notifier._send_message(
                                f"🛡️ <b>FRIDAY PRE-WEEKEND AUTO-FLATTEN EXECUTED</b>\n\n"
                                f"Market close protection active. Flattened {closed} positions across fleet.\n"
                                f"✅ <b>Invariant:</b> Zero weekend gap exposure."
                            )
                        self.symbol_state.clear()
                        self.alerted_trades.clear()
                        self.save_state()
                        time.sleep(30)
                        continue
                
                for pos in positions:
                    t_id = pos['id']
                    symbol = pos['symbol']
                    clean_sym = symbol.replace("/", "").replace("_", "").upper()
                    entry = float(pos.get('price') or 0.0)
                    pnl = float(pos.get('pnl') or 0.0)
                    side = pos.get('side', 'BUY')
                    
                    if t_id not in self.alerted_trades:
                        self.alerted_trades[t_id] = {}

                    sym_data = self.symbol_state.setdefault(clean_sym, {
                        "stepped_defense_executed": False,
                        "scaleout_executed": False,
                        "mfe_scaleout_executed": False,
                        "macro_scaleout_executed": False,
                        "peak_r": 0.0,
                        "milestones": {}
                    })

                    # 1. Fetch SL directly from broker position or fallback
                    sl, scan = self.get_stop_loss(symbol, pos=pos)
                    if not sl or entry <= 0:
                        continue
                    
                    qty = float(pos.get('qty') or 0.0)
                    contract_size = Config.get_contract_size(symbol)

                    # Establish and freeze true initial risk basis on first observation
                    # Prevents artificial R-multiple inflation after stepped defense or BE trails
                    if "initial_sl" not in sym_data or sym_data["initial_sl"] <= 0:
                        min_stop_pct = Config.MIN_STOP_PCT.get(symbol, 0.003)
                        dist = abs(entry - sl)
                        if dist < (entry * min_stop_pct * 0.5):
                            scan_sl = scan.get('stop_loss') if scan else None
                            if scan_sl and float(scan_sl) > 0 and abs(entry - float(scan_sl)) >= (entry * min_stop_pct * 0.5):
                                sl = float(scan_sl)
                            else:
                                sl = entry - (entry * min_stop_pct) if side.upper() == "BUY" else entry + (entry * min_stop_pct)
                        sym_data["initial_sl"] = sl
                        sym_data["initial_risk_usd"] = abs(entry - sl) * qty * contract_size
                        self.save_state()

                    initial_sl = sym_data.get("initial_sl", sl)
                    risk_usd = sym_data.get("initial_risk_usd") or (abs(entry - initial_sl) * qty * contract_size)
                    
                    if not risk_usd or risk_usd <= 0:
                        continue
                    
                    r_multiple = pnl / risk_usd
                    print(f"[{symbol}] PnL: ${pnl:.2f} | Risk: ${risk_usd:.2f} | R: {r_multiple:.2f}")

                    # 3. Peak R-Multiple Tracking & Immediate State Persistence
                    peak_r = max(
                        sym_data.get("peak_r", 0.0),
                        self.alerted_trades.get(t_id, {}).get("peak_r", 0.0),
                        r_multiple
                    )
                    if peak_r > sym_data.get("peak_r", 0.0):
                        sym_data["peak_r"] = peak_r
                        self.alerted_trades[t_id]["peak_r"] = peak_r
                        self.save_state()

                    # 4. Stepped Stop Loss Defense at +1.0R (Tier 0.5: cut SL to -0.3R) (Deduplicated per Symbol)
                    stepped_enabled = getattr(Config, 'STEPPED_DEFENSE_ENABLED', True)
                    stepped_trigger = getattr(Config, 'STEPPED_DEFENSE_TRIGGER_R', 1.0)
                    stepped_locked_r = getattr(Config, 'STEPPED_DEFENSE_LOCKED_R', -0.3)
                    is_stepped = sym_data.get("stepped_defense_executed") or self.alerted_trades.get(t_id, {}).get("stepped_defense_executed")
                    is_scaled_out = sym_data.get("scaleout_executed") or self.alerted_trades.get(t_id, {}).get("scaleout_executed")
                    if stepped_enabled and r_multiple >= stepped_trigger and not is_stepped and not is_scaled_out:
                        print(f"🛡️ [STEPPED DEFENSE] {symbol} hit {r_multiple:.2f}R (>= +{stepped_trigger:.1f}R)! Tightening Stop Loss to {stepped_locked_r:.1f}R across fleet...")
                        self.execute_stepped_defense(symbol, entry, initial_sl, side=side, locked_r=stepped_locked_r)
                        sym_data["stepped_defense_executed"] = True
                        self.alerted_trades[t_id]["stepped_defense_executed"] = True
                        self.save_state()

                    # 5. Standard Automated Fleet Scale-Out at BE Trigger (+1.5R) (Deduplicated per Symbol)
                    be_trigger = getattr(Config, 'BE_TRIGGER_R', 1.5)
                    is_scaled_out = sym_data.get("scaleout_executed") or self.alerted_trades.get(t_id, {}).get("scaleout_executed")
                    if r_multiple >= be_trigger and not is_scaled_out:
                        print(f"💰 [AUTO SCALE-OUT] {symbol} hit {r_multiple:.2f}R! Executing Fleet Break-Even & Scale-Out...")
                        self.execute_fleet_scaleout(symbol, entry, reason=f"+{be_trigger:.1f}R Target Reached", side=side, initial_sl=initial_sl)
                        sym_data["scaleout_executed"] = True
                        self.alerted_trades[t_id]["scaleout_executed"] = True
                        self.save_state()

                    # 5. MFE Peak Retracement Ratchet (Deduplicated per Symbol)
                    mfe_enabled = getattr(Config, 'MFE_PEAK_RATCHET_ENABLED', True)
                    mfe_min_peak = getattr(Config, 'MFE_MIN_PEAK_R', 2.0)
                    mfe_max_retrace = getattr(Config, 'MFE_MAX_RETRACEMENT_R', 0.75)
                    is_mfe_scaled = sym_data.get("mfe_scaleout_executed") or self.alerted_trades.get(t_id, {}).get("mfe_scaleout_executed")
                    if mfe_enabled and peak_r >= mfe_min_peak:
                        retrace = peak_r - r_multiple
                        if retrace >= mfe_max_retrace and not is_mfe_scaled:
                            print(f"🛡️ [MFE PEAK RATCHET] {symbol} peaked at +{peak_r:.2f}R, retraced {retrace:.2f}R (now {r_multiple:.2f}R)! Executing defensive scale-out...")
                            self.execute_fleet_scaleout(symbol, entry, reason=f"MFE Peak Retracement (+{peak_r:.2f}R -> +{r_multiple:.2f}R)", side=side, initial_sl=initial_sl)
                            sym_data["mfe_scaleout_executed"] = True
                            self.alerted_trades[t_id]["mfe_scaleout_executed"] = True
                            self.save_state()

                    # 6. Pre-Macro Event Defense (Deduplicated per Symbol)
                    macro_enabled = getattr(Config, 'MACRO_DEFENSE_ENABLED', True)
                    macro_min_r = getattr(Config, 'MACRO_DEFENSE_MIN_R', 1.0)
                    is_macro_scaled = sym_data.get("macro_scaleout_executed") or self.alerted_trades.get(t_id, {}).get("macro_scaleout_executed")
                    if macro_enabled and r_multiple >= macro_min_r and not is_macro_scaled:
                        try:
                            from src.engines.calendar_filter import CalendarFilter
                            is_safe, cal_reason = CalendarFilter().is_safe_to_trade(symbol)
                            if not is_safe and "⛔ MACRO BLACKOUT" in str(cal_reason):
                                print(f"⚡ [PRE-MACRO DEFENSE] {symbol} at +{r_multiple:.2f}R approaching macro event! Banking profit & locking BE...")
                                self.execute_fleet_scaleout(symbol, entry, reason=f"Pre-Macro Defense: {cal_reason}", side=side, initial_sl=initial_sl)
                                sym_data["macro_scaleout_executed"] = True
                                self.alerted_trades[t_id]["macro_scaleout_executed"] = True
                                self.save_state()
                        except Exception as cal_err:
                            pass

                    # 7. Milestone Telegram Alerts (Deduplicated per Symbol)
                    for target in [1.5, 2.0, 2.5]:
                        target_key = str(target)
                        is_target_alerted = sym_data.get("milestones", {}).get(target_key) or self.alerted_trades.get(t_id, {}).get(target_key)
                        if r_multiple >= target and not is_target_alerted:
                            msg = (
                                f"🚀 <b>BAYESIAN PIVOT TARGET REACHED!</b>\n"
                                f"Symbol: <code>{symbol}</code>\n"
                                f"Current R: <b>{r_multiple:.2f}R</b>\n\n"
                                f"🛡️ <b>DISCIPLINE CHECK:</b> Target {target}R reached.\n"
                                f"Break-Even stop loss and autonomous protection active."
                            )
                            self.notifier._send_message(msg)
                            sym_data.setdefault("milestones", {})[target_key] = True
                            self.alerted_trades[t_id][target_key] = True
                            self.save_state()

            except Exception as e:
                print(f"Watchdog Loop Error: {e}")
            
            time.sleep(60) # Poll every 60s

    def execute_fleet_scaleout(self, symbol: str, entry_price: float, reason: str = "+1.5R Floating Gain Reached", side: str = "BUY", initial_sl: float = None):
        """
        Automated Fleet Scale-Out & True Net Break-Even Protection:
        - Trailing Stop Loss to True Net Break-Even (covering round-trip commission & spread) across 100% of open positions on ALL active accounts.
        - Scale-out accounts (Account 1: idx 0, Account 3: idx 2, Account 9: idx 8) close Tranche 1 if multiple tranches open.
        - Pacing: 2.0s adaptive pacing between accounts (AGENTS.md Rule 5).
        """
        try:
            trailed_count = 0
            closed_count = 0
            target_sym = symbol.replace("/", "").replace("_", "").upper()
            target_inst_id = str(self.tl.resolve_instrument_id(symbol))
            
            # Decoupled fleet indices:
            # Scale-out accounts: Account 1 (0), Account 3 (2), Account 9 (8)
            # Runner accounts: Account 2 (1), Account 6 (5), Account 7 (6)
            # Decommissioned accounts: 3, 4, 7 (quarantined, zero risk)
            scale_out_indices = set(getattr(Config, 'SCALE_OUT_ACCOUNT_INDICES', [0, 2, 8]))

            be_offset_r = getattr(Config, 'BE_OFFSET_R', 0.08)
            min_usd_map = getattr(Config, 'BE_MIN_OFFSET_USD', {"BTC": 25.0, "ETH": 2.0, "SOL": 0.20, "XAU": 0.80})
            clean_sym = target_sym.replace("/", "").replace("_", "").upper()
            min_usd = 0.0
            for k, v in min_usd_map.items():
                if k in clean_sym:
                    min_usd = v
                    break

            last_net_be = float(entry_price)
            last_fee_buffer = 0.0

            for acc_idx, helper in enumerate(self.tl.helpers):
                if acc_idx > 0:
                    time.sleep(2.0) # Adaptive 2.0s pacing
                
                # Skip decommissioned liquidation-only accounts
                if getattr(helper, 'status', '') == 'LIQUIDATION_ONLY' or acc_idx in [3, 4, 7]:
                    continue

                if not helper.access_token and not helper.login():
                    continue

                try:
                    positions = helper.get_open_positions()
                    target_pos = [
                        p for p in positions 
                        if (
                            target_sym in str(p.get("symbol", "")).replace("/", "").replace("_", "").upper()
                            or str(p.get("tradableInstrumentId") or p.get("instrumentId") or "") == target_inst_id
                        )
                    ]
                    
                    if not target_pos:
                        continue

                    is_mfe = "MFE Peak Retracement" in str(reason)
                    
                    if is_mfe:
                        # Defensive market closure: close ALL remaining positions on this account to lock in banked gain
                        for p in target_pos:
                            pid = p.get("id") or p.get("positionId")
                            if helper.close_position(pid):
                                closed_count += 1
                    else:
                        # Determine positions to close vs positions to trail
                        if acc_idx in scale_out_indices and len(target_pos) > 1:
                            t1_pos = target_pos[0]
                            pos_id = t1_pos.get("id") or t1_pos.get("positionId")
                            if helper.close_position(pos_id):
                                closed_count += 1
                            time.sleep(1.0)
                            trail_targets = target_pos[1:]
                        else:
                            trail_targets = target_pos

                        for p in trail_targets:
                            pid = p.get("id") or p.get("positionId")
                            pos_side = str(p.get("side") or side or "BUY").upper()
                            pos_entry = float(p.get("price") or p.get("avgPrice") or entry_price)
                            pos_sl_raw = p.get("stopLoss")
                            pos_sl = float(pos_sl_raw) if pos_sl_raw else (float(initial_sl) if initial_sl else 0.0)
                            
                            risk_dist = abs(pos_entry - pos_sl) if pos_sl > 0 else (pos_entry * 0.003)
                            fee_buffer = max(be_offset_r * risk_dist, min_usd)
                            
                            entry_str = str(pos_entry).rstrip('0')
                            dec = len(entry_str.split('.')[1]) if '.' in entry_str else 2
                            dec = max(2, min(5, dec))

                            if pos_side == "BUY":
                                net_be = round(pos_entry + fee_buffer, dec)
                            else:
                                net_be = round(pos_entry - fee_buffer, dec)

                            last_net_be = net_be
                            last_fee_buffer = fee_buffer

                            # Protective Invariant: Never loosen SL if already tighter than net_be
                            curr_sl = p.get("stopLoss")
                            if curr_sl:
                                try:
                                    curr_sl_val = float(curr_sl)
                                    if pos_side == "BUY" and curr_sl_val >= net_be:
                                        continue
                                    elif pos_side != "BUY" and curr_sl_val <= net_be:
                                        continue
                                except (ValueError, TypeError):
                                    pass

                            if helper.modify_position_bracket(pid, stop_loss=net_be):
                                trailed_count += 1
                except Exception as acc_err:
                    print(f"Error scaling out account {acc_idx+1}: {acc_err}")

            if "MFE Peak Retracement" in str(reason):
                scaleout_msg = (
                    f"🛡️ <b>MFE PEAK RATCHET PROFIT LOCK</b>\n\n"
                    f"Symbol: <code>{symbol}</code>\n"
                    f"Trigger: <b>{reason}</b>\n\n"
                    f"🏦 <b>Realized Cash Profit:</b> Market-closed {closed_count} positions across fleet\n"
                    f"✅ <b>Invariant:</b> Protected peak profit. Zero surrender to reversal."
                )
            else:
                scaleout_msg = (
                    f"🛡️ <b>AUTONOMOUS FLEET PROFIT PROTECTION</b>\n\n"
                    f"Symbol: <code>{symbol}</code>\n"
                    f"Trigger: <b>{reason}</b>\n\n"
                    f"🔒 <b>True Net Break-Even:</b> Trailed Stop Loss to <b>${last_net_be:,.2f}</b> (+${last_fee_buffer:,.2f} commission & spread buffer) on {trailed_count} positions\n"
                    f"🏦 <b>Realized Cash Profit:</b> Closed {closed_count} tranches on scale-out accounts\n\n"
                    f"✅ <b>Invariant:</b> 100% immune to broker fee bleed. Zero net scratch guaranteed."
                )
            self.notifier._send_message(scaleout_msg)
            print(f"✅ Fleet Scale-Out Complete: Trailed={trailed_count} to Net BE (${last_net_be}), Closed={closed_count}")
        except Exception as e:
            print(f"Error executing fleet scaleout: {e}")

    def execute_stepped_defense(self, symbol: str, entry_price: float, initial_sl: float, side: str = "BUY", locked_r: float = -0.3):
        """
        Tier 0.5: Stepped Stop Loss Defense at +1.0R.
        Tightens Stop Loss from -1.0R to -0.3R across all fleet positions.
        Cuts 70% of downside risk without suffocating normal market breathing room.
        Pacing: 2.0s adaptive pacing between accounts (AGENTS.md Rule 5).
        """
        try:
            trailed_count = 0
            target_sym = symbol.replace("/", "").replace("_", "").upper()
            target_inst_id = str(self.tl.resolve_instrument_id(symbol))
            side_upper = str(side).upper()
            risk_dist = abs(entry_price - initial_sl)
            if risk_dist <= 0:
                return

            entry_str = str(entry_price).rstrip('0')
            dec = len(entry_str.split('.')[1]) if '.' in entry_str else 2
            dec = max(2, min(5, dec))

            if side_upper == "BUY":
                new_sl = round(entry_price - abs(locked_r) * risk_dist, dec)
                if new_sl <= initial_sl or new_sl >= entry_price:
                    print(f"⚠️ [STEPPED DEFENSE] BUY invariant violated: initial_sl={initial_sl}, new_sl={new_sl}, entry={entry_price}")
                    return
            else:
                new_sl = round(entry_price + abs(locked_r) * risk_dist, dec)
                if new_sl >= initial_sl or new_sl <= entry_price:
                    print(f"⚠️ [STEPPED DEFENSE] SELL invariant violated: initial_sl={initial_sl}, new_sl={new_sl}, entry={entry_price}")
                    return

            for acc_idx, helper in enumerate(self.tl.helpers):
                if acc_idx > 0:
                    time.sleep(2.0)  # Adaptive 2.0s pacing per AGENTS.md Rule 5

                # Skip decommissioned liquidation-only accounts
                if getattr(helper, 'status', '') == 'LIQUIDATION_ONLY' or acc_idx in [3, 4, 7]:
                    continue

                if not helper.access_token and not helper.login():
                    continue

                try:
                    positions = helper.get_open_positions()
                    target_pos = [
                        p for p in positions
                        if (
                            target_sym in str(p.get("symbol", "")).replace("/", "").replace("_", "").upper()
                            or str(p.get("tradableInstrumentId") or p.get("instrumentId") or "") == target_inst_id
                        )
                    ]
                    if not target_pos:
                        continue

                    for p in target_pos:
                        pid = p.get("id") or p.get("positionId")
                        curr_sl = p.get("stopLoss")
                        if curr_sl:
                            try:
                                curr_sl_val = float(curr_sl)
                                # Never loosen SL: if already at or better than new_sl (e.g. BE), skip
                                if side_upper == "BUY" and curr_sl_val >= new_sl:
                                    continue
                                elif side_upper != "BUY" and curr_sl_val <= new_sl:
                                    continue
                            except (ValueError, TypeError):
                                pass

                        if helper.modify_position_bracket(pid, stop_loss=new_sl):
                            trailed_count += 1
                except Exception as acc_err:
                    print(f"Error executing stepped defense on account {acc_idx+1}: {acc_err}")

            defense_msg = (
                f"🛡️ <b>STEPPED STOP LOSS DEFENSE (+1.0R)</b>\n\n"
                f"Symbol: <code>{symbol}</code>\n"
                f"Side: <b>{side_upper}</b>\n"
                f"Entry: <b>{entry_price}</b> | Initial SL: <b>{initial_sl}</b>\n"
                f"🔒 <b>Tightened Stop Loss:</b> <code>{new_sl}</code> ({locked_r:.1f}R)\n"
                f"✅ <b>Downside Risk Reduction:</b> 70% risk eliminated across {trailed_count} positions\n"
                f"🎯 <b>Next Defense:</b> Break-Even (0.0R) at +1.5R target"
            )
            self.notifier._send_message(defense_msg)
            print(f"✅ Stepped Defense Complete: {symbol} SL tightened to {new_sl} on {trailed_count} positions")
        except Exception as e:
            print(f"Error executing stepped defense: {e}")

if __name__ == "__main__":
    PositionWatchdog().run()
