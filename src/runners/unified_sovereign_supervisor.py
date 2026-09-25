import os
import sys
import time
import signal
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.core.config import Config

# Configure unified logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - (%(threadName)s) - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/unified_supervisor.log")
    ]
)
logger = logging.getLogger("UnifiedSupervisor")

class UnifiedSovereignSupervisor:
    """
    Unified Sovereign Supervisor Daemon.
    Consolidates:
      1. Alpha Sweep & Inducement Scanning (Multi-Asset SMC & Shadow Tournament)
      2. Active Position Watchdog & Autonomous Fleet Scale-Out (50% scale-out @ +1.5R)
      3. Shadow Tournament Resolution & Maintenance
    Runs in a single lightweight Python process (~120 MB RAM vs 800 MB across 10 processes).
    """

    def __init__(self):
        self.running = True
        self.scanner = None
        self.watchdog = None
        self.tl_client = None
        self._setup_signals()

    def get_tl_client(self):
        if self.tl_client is None:
            from src.clients.tl_client import TradeLockerClient
            self.tl_client = TradeLockerClient()
        return self.tl_client

    def _setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame):
        logger.info("🛑 Received termination signal. Shutting down Unified Supervisor gracefully...")
        self.running = False

    def get_adaptive_scan_interval(self) -> int:
        """
        Session-Adaptive Sleep Pacing:
        - Weekends (Saturday/Sunday UTC): 300s (5m) — Live execution firewalled
        - Active London / NY Killzone (07:00-10:00, 13:00-16:00 UTC): 60s (1m) — Prime execution
        - Off-Hours Weekday: 180s (3m) — Preserves CPU cycles
        """
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday() # 5=Sat, 6=Sun
        hour = now_utc.hour

        if weekday >= 5:
            return 300 # 5 minutes on weekends ($0 live risk)
        
        # London Killzone (07:00-10:00 UTC) or NY AM/PM Killzone (12:00-19:00 UTC)
        is_killzone = (7 <= hour < 10) or (12 <= hour < 20)
        if is_killzone:
            return 60 # 1 minute during active institutional sessions
        
        return 180 # 3 minutes during quiet off-hours

    # ── WORKER 1: Alpha Sweep & Inducement Scanner ──
    def run_scanner_worker(self):
        logger.info("🚀 [Worker: Scanner] Starting Alpha Sweep & Inducement worker...")
        try:
            from src.engines.alpha_sweep_scanner import AlphaSweepScanner
            self.scanner = AlphaSweepScanner()
        except Exception as e:
            logger.error(f"Failed to initialize AlphaSweepScanner: {e}")
            return

        while self.running:
            try:
                live_symbols = list(getattr(Config, 'SYMBOLS', ['BTC/USD']))
                shadow_symbols = list(getattr(Config, 'SHADOW_SYMBOLS', ['ETH/USD', 'SOL/USD']))
                all_symbols = list(dict.fromkeys(live_symbols + shadow_symbols))

                # Trailing defense and profit protection are handled exclusively by the dedicated WatchdogThread
                # to avoid duplicate polling, HTTP 429 rate limits, and conflicting Telegram notifications.

                # 2. Scan all configured symbols (Live + Shadow Trackers)
                for sym in all_symbols:
                    if not self.running:
                        break
                    try:
                        is_shadow_asset = (sym in shadow_symbols and sym not in live_symbols)
                        setup = self.scanner.scan_symbol(sym, is_shadow=is_shadow_asset)
                        if setup:
                            prefix = "👻 [SHADOW SETUP]" if is_shadow_asset else "🎯 [SCANNER SETUP]"
                            logger.info(f"{prefix} {sym}: {setup.get('pattern')} | Conviction={setup.get('conviction_score')}")
                    except Exception as e:
                        logger.error(f"Error scanning {sym}: {e}")

            except Exception as e:
                logger.error(f"Scanner worker loop error: {e}")

            # Adaptive Sleep
            interval = self.get_adaptive_scan_interval()
            logger.info(f"💤 [Worker: Scanner] Sleeping {interval}s (Adaptive Session Pacing)...")
            slept = 0
            while slept < interval and self.running:
                time.sleep(2)
                slept += 2

    # ── WORKER 2: Position Watchdog & Autonomous Fleet Scale-Out ──
    def run_watchdog_worker(self):
        logger.info("🛡️ [Worker: Watchdog] Starting Risk & Fleet Scale-Out watchdog...")
        try:
            from scripts.position_watchdog import PositionWatchdog
            self.watchdog = PositionWatchdog(tl_client=self.get_tl_client())
        except Exception as e:
            logger.error(f"Failed to initialize PositionWatchdog: {e}")
            return

        while self.running:
            try:
                positions = self.watchdog.tl.get_open_positions()
                if not positions:
                    if getattr(self.watchdog, 'symbol_state', None) or getattr(self.watchdog, 'alerted_trades', None):
                        self.watchdog.symbol_state.clear()
                        self.watchdog.alerted_trades.clear()
                        self.watchdog.save_state()
                else:
                    # Friday Pre-Weekend Auto-Flatten Gate: Flatten fleet at Friday 20:00 UTC to eliminate weekend gap slippage
                    now_utc = datetime.now(timezone.utc)
                    if now_utc.weekday() == 4 and now_utc.hour >= 20:
                        logger.warning("🛡️ [FRIDAY PRE-WEEKEND AUTO-FLATTEN] Friday 20:00 UTC reached! Flattening all open positions across fleet to prevent weekend gap slippage...")
                        closed = self.watchdog.tl.close_all_fleet_positions()
                        self.watchdog.notifier._send_message(
                            f"🛡️ <b>FRIDAY PRE-WEEKEND AUTO-FLATTEN EXECUTED</b>\n\n"
                            f"Market close protection active. Flattened {closed} positions across fleet.\n"
                            f"✅ <b>Invariant:</b> Zero weekend gap exposure."
                        )
                        self.watchdog.symbol_state.clear()
                        self.watchdog.alerted_trades.clear()
                        self.watchdog.save_state()
                        time.sleep(30)
                        continue

                    for pos in positions:
                        t_id = pos['id']
                        symbol = pos['symbol']
                        clean_sym = symbol.replace("/", "").replace("_", "").upper()
                        entry = float(pos.get('price') or 0.0)
                        pnl = float(pos.get('pnl') or 0.0)
                        side = pos.get('side', 'BUY')
                        
                        if t_id not in self.watchdog.alerted_trades:
                            self.watchdog.alerted_trades[t_id] = {}

                        sym_data = self.watchdog.symbol_state.setdefault(clean_sym, {
                            "stepped_defense_executed": False,
                            "scaleout_executed": False,
                            "mfe_scaleout_executed": False,
                            "macro_scaleout_executed": False,
                            "peak_r": 0.0,
                            "milestones": {}
                        })

                        sl, scan = self.watchdog.get_stop_loss(symbol, pos=pos)
                        if not sl or entry <= 0:
                            continue
                        
                        qty = float(pos.get('qty') or 0.0)
                        contract_size = Config.get_contract_size(symbol)

                        # Establish and freeze true initial stop loss level and master entry on first observation
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
                            scan_entry = float(scan.get('price') or 0.0) if scan else 0.0
                            sym_data["initial_entry"] = scan_entry if scan_entry > 0 else entry
                            self.watchdog.save_state()

                        initial_sl = sym_data.get("initial_sl", sl)
                        initial_entry = sym_data.get("initial_entry", entry)
                        stop_dist = abs(initial_entry - initial_sl)
                        if stop_dist <= 0:
                            continue

                        # Per-Position Risk Basis (IMMUNE TO CROSS-ACCOUNT LOT SIZE CONTAMINATION)
                        pos_risk_usd = stop_dist * qty * contract_size
                        if pos_risk_usd <= 0:
                            continue

                        r_multiple = pnl / pos_risk_usd

                        # Autonomous Adversarial Quality Invariant: Price-Geometry Ground Truth Cross-Validation
                        try:
                            from src.core.adversarial_quality_loop import PriceGeometryGroundTruth
                            current_price = entry + (pnl / (qty * contract_size)) if side.upper() == "BUY" else entry - (pnl / (qty * contract_size))
                            geom_r = PriceGeometryGroundTruth.compute_geom_r(initial_entry, initial_sl, current_price, side)
                            is_valid, authoritative_r, reason = PriceGeometryGroundTruth.validate_r_multiple_integrity(r_multiple, geom_r)
                            if not is_valid:
                                logger.warning(reason)
                                r_multiple = authoritative_r
                                # Invariant: Never allow peak_r to remain inflated above authoritative price geometry
                                if sym_data.get("peak_r", 0.0) > authoritative_r:
                                    sym_data["peak_r"] = authoritative_r
                                    if t_id in self.watchdog.alerted_trades:
                                        self.watchdog.alerted_trades[t_id]["peak_r"] = authoritative_r
                                    self.watchdog.save_state()
                        except Exception as val_err:
                            pass

                        logger.info(f"📊 [OPEN POSITION] {symbol} (Qty: {qty}) PnL: ${pnl:.2f} | Risk: ${pos_risk_usd:.2f} | R: {r_multiple:.2f}R")

                        # 1. Peak R Tracking & Immediate State Persistence
                        peak_r = max(
                            sym_data.get("peak_r", 0.0),
                            self.watchdog.alerted_trades.get(t_id, {}).get("peak_r", 0.0),
                            r_multiple
                        )
                        if peak_r > sym_data.get("peak_r", 0.0):
                            sym_data["peak_r"] = peak_r
                            self.watchdog.alerted_trades[t_id]["peak_r"] = peak_r
                            self.watchdog.save_state()

                        # 2. Stepped Stop Loss Defense at +1.0R (Tier 0.5: cut SL to -0.3R) (Deduplicated per Symbol)
                        stepped_enabled = getattr(Config, 'STEPPED_DEFENSE_ENABLED', True)
                        stepped_trigger = getattr(Config, 'STEPPED_DEFENSE_TRIGGER_R', 1.0)
                        stepped_locked_r = getattr(Config, 'STEPPED_DEFENSE_LOCKED_R', -0.3)
                        is_stepped = sym_data.get("stepped_defense_executed") or self.watchdog.alerted_trades.get(t_id, {}).get("stepped_defense_executed")
                        is_scaled_out = sym_data.get("scaleout_executed") or self.watchdog.alerted_trades.get(t_id, {}).get("scaleout_executed")
                        if stepped_enabled and r_multiple >= stepped_trigger and not is_stepped and not is_scaled_out:
                            logger.info(f"🛡️ [STEPPED DEFENSE] {symbol} reached {r_multiple:.2f}R! Tightening Stop Loss to {stepped_locked_r:.1f}R across fleet...")
                            self.watchdog.execute_stepped_defense(symbol, entry, initial_sl, side=side, locked_r=stepped_locked_r)
                            sym_data["stepped_defense_executed"] = True
                            self.watchdog.alerted_trades[t_id]["stepped_defense_executed"] = True
                            self.watchdog.save_state()

                        # 3. Autonomous Fleet Scale-Out at BE Trigger (+1.5R) (Deduplicated per Symbol)
                        be_trigger = getattr(Config, 'BE_TRIGGER_R', 1.5)
                        is_scaled_out = sym_data.get("scaleout_executed") or self.watchdog.alerted_trades.get(t_id, {}).get("scaleout_executed")
                        if r_multiple >= be_trigger and not is_scaled_out:
                            logger.info(f"💰 [AUTO SCALE-OUT] {symbol} reached {r_multiple:.2f}R! Executing 50% fleet closure & Break-Even trail...")
                            self.watchdog.execute_fleet_scaleout(symbol, entry, reason=f"+{be_trigger:.1f}R Target Reached", side=side, initial_sl=initial_sl)
                            sym_data["scaleout_executed"] = True
                            self.watchdog.alerted_trades[t_id]["scaleout_executed"] = True
                            self.watchdog.save_state()

                        # 4. MFE Peak Retracement Ratchet (Deduplicated per Symbol)
                        mfe_enabled = getattr(Config, 'MFE_PEAK_RATCHET_ENABLED', True)
                        mfe_min_peak = getattr(Config, 'MFE_MIN_PEAK_R', 2.0)
                        mfe_max_retrace = getattr(Config, 'MFE_MAX_RETRACEMENT_R', 0.75)
                        is_mfe_scaled = sym_data.get("mfe_scaleout_executed") or self.watchdog.alerted_trades.get(t_id, {}).get("mfe_scaleout_executed")
                        if mfe_enabled and peak_r >= mfe_min_peak:
                            retrace = peak_r - r_multiple
                            if retrace >= mfe_max_retrace and not is_mfe_scaled:
                                logger.warning(f"🛡️ [MFE PEAK RATCHET] {symbol} peaked at +{peak_r:.2f}R, retraced {retrace:.2f}R! Banking profit at market...")
                                self.watchdog.execute_fleet_scaleout(symbol, entry, reason=f"MFE Peak Retracement (+{peak_r:.2f}R -> +{r_multiple:.2f}R)", side=side, initial_sl=initial_sl)
                                sym_data["mfe_scaleout_executed"] = True
                                self.watchdog.alerted_trades[t_id]["mfe_scaleout_executed"] = True
                                self.watchdog.save_state()

                        # 4. Pre-Macro Event Defense (Deduplicated per Symbol)
                        macro_enabled = getattr(Config, 'MACRO_DEFENSE_ENABLED', True)
                        macro_min_r = getattr(Config, 'MACRO_DEFENSE_MIN_R', 1.0)
                        is_macro_scaled = sym_data.get("macro_scaleout_executed") or self.watchdog.alerted_trades.get(t_id, {}).get("macro_scaleout_executed")
                        if macro_enabled and r_multiple >= macro_min_r and not is_macro_scaled:
                            try:
                                from src.engines.calendar_filter import CalendarFilter
                                is_safe, cal_reason = CalendarFilter().is_safe_to_trade(symbol)
                                if not is_safe and "⛔ MACRO BLACKOUT" in str(cal_reason):
                                    logger.warning(f"⚡ [PRE-MACRO DEFENSE] {symbol} at +{r_multiple:.2f}R approaching macro event! Banking profit & locking BE...")
                                    self.watchdog.execute_fleet_scaleout(symbol, entry, reason=f"Pre-Macro Defense: {cal_reason}", side=side, initial_sl=initial_sl)
                                    sym_data["macro_scaleout_executed"] = True
                                    self.watchdog.alerted_trades[t_id]["macro_scaleout_executed"] = True
                                    self.watchdog.save_state()
                            except Exception as cal_err:
                                pass

                        # 5. Milestone Telegram Alerts (Deduplicated per Symbol & Account-Tagged)
                        acc_email = pos.get('account_email', '')
                        if "s79qv3xetj" in acc_email: acc_label = "Account #1 ($25k)"
                        elif "498svcbpfi" in acc_email: acc_label = "Account #2 ($50k)"
                        elif "q20gxm287x" in acc_email: acc_label = "Account #3 ($25k)"
                        elif "dwundrtxjv" in acc_email: acc_label = "Account #6 ($50k)"
                        elif "875do5esrd" in acc_email: acc_label = "Account #7 ($25k)"
                        elif "jfcuue7er3" in acc_email: acc_label = "Account #9 ($50k Lead)"
                        elif acc_email: acc_label = f"Account ({acc_email.split('@')[0]})"
                        else: acc_label = f"Account {pos.get('account_id', 'Fleet Lead')}"

                        unalerted_milestones = [
                            target for target in [1.5, 2.0, 2.5]
                            if r_multiple >= target and not (
                                sym_data.get("milestones", {}).get(str(target))
                                or self.watchdog.alerted_trades.get(t_id, {}).get(str(target))
                            )
                        ]

                        if unalerted_milestones:
                            highest_target = max(unalerted_milestones)
                            surged_past = [m for m in unalerted_milestones if m < highest_target]
                            surged_note = f" (surged past {', '.join(f'+{m:.1f}R' for m in surged_past)})" if surged_past else ""
                            msg = (
                                f"🎯 <b>INTERMEDIATE MILESTONE: +{highest_target:.1f}R REACHED</b>\n"
                                f"Symbol: <code>{symbol}</code> ({side})\n"
                                f"Account: <b>{acc_label}</b> | Size: <b>{qty} lots</b>\n"
                                f"Current Floating Gain: <b>+{r_multiple:.2f}R</b> (${pnl:,.2f}) (Trade Still Active)\n\n"
                                f"🛡️ <b>DISCIPLINE CHECK:</b> Milestone +{highest_target:.1f}R cleared{surged_note}.\n"
                                "Autonomous fleet scale-out and trailing stop active. Position running toward full TP."
                            )
                            self.watchdog.notifier._send_message(msg)
                            for target in unalerted_milestones:
                                sym_data.setdefault("milestones", {})[str(target)] = True
                                self.watchdog.alerted_trades[t_id][str(target)] = True
                            self.watchdog.save_state()

                # Sleep 60s when positions exist, 120s when flat
                sleep_time = 60 if positions else 120
                slept = 0
                while slept < sleep_time and self.running:
                    time.sleep(2)
                    slept += 2

            except Exception as e:
                logger.error(f"Watchdog worker loop error: {e}")
                time.sleep(30)

    # ── WORKER 3: Maintenance & Shadow Reconciler ──
    def run_maintenance_worker(self):
        logger.info("🧹 [Worker: Maintenance] Starting Shadow Tournament & DB Maintenance worker...")
        while self.running:
            try:
                # 1. Update SQLite WAL checkpoint to keep db small
                from src.core.database import get_db_connection
                conn = get_db_connection()
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                conn.close()
                logger.debug("SQLite WAL checkpoint completed.")

                # 2. Automated System Hygiene Sweep (Caches, PyCache, MCP Debuggers, Memory Health)
                try:
                    from scripts.maintenance.system_hygiene import sweep_system_hygiene
                    hygiene_res = sweep_system_hygiene()
                    cleaned_mcp = hygiene_res.get("mcp_cleaned", 0)
                    freed_caches = hygiene_res.get("caches_freed_mb", {})
                    py_cleaned = hygiene_res.get("pycache_dirs_removed", 0)
                    if cleaned_mcp > 0 or freed_caches or py_cleaned > 0:
                        logger.info(f"🧹 [System Hygiene] Auto-sweep: {cleaned_mcp} MCP killed, {len(freed_caches)} caches purged, {py_cleaned} pycaches cleared.")
                except Exception as hyg_err:
                    logger.debug(f"System hygiene sweep notice: {hyg_err}")

                # 3. Continuous Shadow Trade Resolution & Lifecycle Management
                try:
                    from src.engines.counterfactual_tracker import CounterfactualTracker
                    cf_tracker = CounterfactualTracker()
                    resolved_ct = cf_tracker.evaluate_open_shadow_trades(self.scanner)
                    if resolved_ct > 0:
                        logger.info(f"🏁 [Shadow Lifecycle] Automatically resolved {resolved_ct} open counterfactual trades.")
                except Exception as cf_err:
                    logger.debug(f"Shadow trade resolution error: {cf_err}")

                # 4. Synchronize Broker Positions & Closed History into SQLite journal
                try:
                    from datetime import datetime, timezone
                    now_utc = datetime.now(timezone.utc)
                    is_weekend = now_utc.weekday() in [5, 6]

                    tl_sync = self.get_tl_client()
                    open_pos = tl_sync.get_open_positions()

                    # Skip ordersHistory during weekend maintenance window to avoid 503 log spam
                    history = []
                    if not is_weekend:
                        try:
                            history = tl_sync.get_recent_history(hours=72)
                        except Exception as _hist_err:
                            logger.debug(f"History fetch notice: {_hist_err}")

                    if open_pos or history:
                        sync_conn = get_db_connection()
                        sc = sync_conn.cursor()
                        for t in (open_pos or []):
                            entry_time = t.get('entry_time') or datetime.now(timezone.utc).isoformat()
                            sc.execute("""
                                INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, price, status, ai_grade, mentor_feedback, strategy)
                                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 0.0, 'Synced Active Trade', 'SYSTEM')
                                ON CONFLICT(trade_id) DO UPDATE SET pnl = excluded.pnl, status = 'OPEN'
                            """, (entry_time, str(t['id']), str(t['symbol']), str(t['side']), float(t.get('pnl', 0.0)), float(t.get('price', 0.0))))
                        for t in (history or []):
                            close_time = t.get('close_time') or datetime.now(timezone.utc).isoformat()
                            sc.execute("""
                                INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, price, status, ai_grade, mentor_feedback, strategy)
                                VALUES (?, ?, ?, ?, ?, ?, 'CLOSED', 0.0, 'Synced History', 'FLEET_SYNC')
                                ON CONFLICT(trade_id) DO UPDATE SET pnl = excluded.pnl, status = 'CLOSED'
                            """, (close_time, str(t['id']), str(t['symbol']), str(t['side']), float(t.get('pnl', 0.0)), float(t.get('price', 0.0))))
                        sync_conn.commit()
                        sync_conn.close()
                        logger.info(f"📊 [Broker Sync] Synchronized {len(open_pos or [])} active positions & {len(history or [])} closed trades into SQLite journal.")
                except Exception as sync_err:
                    logger.debug(f"Broker journal sync notice: {sync_err}")

            except Exception as e:
                logger.debug(f"Maintenance error: {e}")

            # Run broker history sync every 10 minutes (600s) to eliminate unnecessary API load & HTTP 429s
            slept = 0
            while slept < 600 and self.running:
                time.sleep(5)
                slept += 5

    # ── WORKER 4: Sovereign Quality Governor & Invariant Sentry ──
    def run_quality_governor_worker(self):
        logger.info("🛡️ [Worker: Quality Governor] Starting Continuous Runtime Invariant Sentry...")
        try:
            from src.core.quality_governor import QualityGovernor
            governor = QualityGovernor()
        except Exception as e:
            logger.error(f"Failed to initialize QualityGovernor: {e}")
            return

        while self.running:
            try:
                report = governor.run_runtime_audit(tl_client=self.get_tl_client())
                if report.get("status") == "CRITICAL":
                    logger.critical(f"🚨 [QUALITY GOVERNOR ALARM] {len(report.get('violations', []))} Invariant Violation(s) Detected!")
            except Exception as e:
                logger.error(f"Quality Governor worker loop error: {e}")

            # Sleep 60 seconds between runtime health audits
            slept = 0
            while slept < 60 and self.running:
                time.sleep(2)
                slept += 2

    def ensure_mlx_lora_daemon(self):
        """
        Guarantees that the local Apple Silicon MLX LoRA inference server (port 8080)
        is online and responding. If offline on macOS, auto-spawns scripts/start_mlx_server.sh.
        """
        if sys.platform != "darwin":
            return

        url = "http://127.0.0.1:8080/v1/models"
        try:
            import requests
            res = requests.get(url, timeout=1.0)
            if res.status_code == 200:
                logger.info("🤖 [MLX LoRA Lifecycle] Apple Silicon MLX LoRA server verified healthy on port 8080.")
                return
        except Exception:
            pass

        logger.info("🤖 [MLX LoRA Lifecycle] Server offline on port 8080. Spawning daemon via scripts/start_mlx_server.sh...")
        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../scripts/start_mlx_server.sh"))
        if os.path.exists(script_path):
            try:
                import subprocess
                subprocess.Popen(
                    ["/bin/bash", script_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
                )
                import requests
                for _ in range(8):
                    time.sleep(1)
                    try:
                        r = requests.get(url, timeout=1.0)
                        if r.status_code == 200:
                            logger.info("🤖 [MLX LoRA Lifecycle] MLX LoRA server successfully auto-spawned and verified on port 8080.")
                            return
                    except Exception:
                        pass
                logger.warning("🤖 [MLX LoRA Lifecycle] Spawned MLX LoRA script, awaiting initialization...")
            except Exception as e:
                logger.error(f"Failed to auto-spawn MLX LoRA server: {e}")
        else:
            logger.warning(f"MLX startup script not found at {script_path}")

    def start(self):
        logger.info("👑 =========================================================")
        logger.info("👑 SOVEREIGN UNIFIED SUPERVISOR STARTING")
        logger.info(f"👑 PID: {os.getpid()} | Adaptive Pacing: Active | Memory Cap: ~120 MB")
        logger.info("👑 =========================================================")

        # 0. Ensure Apple Silicon MLX LoRA Inference Server is Active
        self.ensure_mlx_lora_daemon()

        # Run Pre-Flight Invariant Audit before launching workers
        try:
            from src.core.quality_governor import QualityGovernor
            gov = QualityGovernor()
            audit = gov.run_preflight_audit()
            if audit["status"] != "PASS":
                logger.warning(f"⚠️ [PRE-FLIGHT WARNING] Pre-flight audit flagged {len(audit['all_issues'])} issue(s).")
            else:
                logger.info("✅ [PRE-FLIGHT PASSED] All broker and codebase invariants verified.")
        except Exception as pf_err:
            logger.error(f"Pre-flight audit execution error: {pf_err}")

        # Spawn macOS sleep prevention assertion (caffeinate) to protect runtime watchdogs
        self.caffeinate_proc = None
        if sys.platform == "darwin":
            try:
                import subprocess
                self.caffeinate_proc = subprocess.Popen(
                    ["caffeinate", "-i", "-s", "-m", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info(f"☕ [Host Power Sentry] macOS caffeinate assertion active (PID: {self.caffeinate_proc.pid}) — sleep disabled.")
            except Exception as caf_err:
                logger.debug(f"macOS caffeinate notice: {caf_err}")

        t_scanner = threading.Thread(target=self.run_scanner_worker, name="ScannerThread", daemon=True)
        t_watchdog = threading.Thread(target=self.run_watchdog_worker, name="WatchdogThread", daemon=True)
        t_maint = threading.Thread(target=self.run_maintenance_worker, name="MaintThread", daemon=True)
        t_governor = threading.Thread(target=self.run_quality_governor_worker, name="QualityGovernorThread", daemon=True)

        t_scanner.start()
        t_watchdog.start()
        t_maint.start()
        t_governor.start()

        # Keep main thread alive
        while self.running:
            time.sleep(1)

        if self.caffeinate_proc:
            try:
                self.caffeinate_proc.terminate()
            except Exception:
                pass

        logger.info("Unified Supervisor shutdown complete.")

if __name__ == "__main__":
    import argparse
    from src.core.process_lock import MasterProcessLock

    parser = argparse.ArgumentParser(description="Unified Sovereign Supervisor")
    parser.add_argument("--test", action="store_true", help="Run a single test cycle and exit")
    args = parser.parse_args()

    with MasterProcessLock(runner_name="UnifiedSovereignSupervisor"):
        supervisor = UnifiedSovereignSupervisor()
        if args.test:
            logger.info("Running single test cycle across all workers...")
            supervisor.get_adaptive_scan_interval()
            from src.engines.alpha_sweep_scanner import AlphaSweepScanner
            s = AlphaSweepScanner()
            s.scan_symbol("BTC/USD")
            logger.info("Test cycle completed successfully.")
        else:
            supervisor.start()
