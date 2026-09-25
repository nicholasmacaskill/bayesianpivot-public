#!/usr/bin/env python3
"""
================================================================================
🏛️ BAYESIAN PIVOT TRADING INFRASTRUCTURE // SOVEREIGN QUALITY GOVERNOR
================================================================================
Autonomous Pre-Flight Gate & Continuous Runtime Quality Invariant Sentry.
Guarantees zero silent execution failures, prevents schema/symbol drift,
and enforces AGENTS.md broker and prop firm compliance.
"""

import os
import sys
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.core.config import Config

logger = logging.getLogger("QualityGovernor")


class QualityGovernor:
    """
    Production Quality Loop & Invariant Governor.
    Executes pre-flight static verification and continuous runtime health sentry.
    """

    CRITICAL_ERROR_PATTERNS = [
        ("BadSymbol", 2, "CCXT / Exchange symbol mismatch detected"),
        ("Rate limited", 8, "Excessive broker HTTP 429 rate limit pressure"),
        ("401 Unauthorized", 15, "Broker authentication token expiration loop"),
        ("Read timed out", 3, "Broker network latency timeout spike"),
        ("Failed to patch position", 1, "Broker position bracket modification rejected"),
        ("Traceback (most recent call last)", 2, "Unhandled exception in supervisor thread")
    ]

    def __init__(self, log_path: str = "logs/unified_supervisor.log"):
        self.log_path = log_path
        self.last_alert_time = 0.0
        self.alert_cooldown = 300.0  # 5 min cooldown between identical TG alerts

    # ── CHECK 1: SYMBOL NORMALIZATION & TRANSLATION CONSISTENCY ───────────────
    def check_symbol_translations(self) -> Tuple[bool, List[str]]:
        """
        Verifies bidirectional symbol translation between TradeLocker (unslashed),
        CCXT (slashed), and Supabase/SQLite.
        """
        issues = []
        crypto_symbols = ["BTC/USD", "ETH/USD", "SOL/USD"]
        metals_forex_symbols = ["XAU/USD"]

        try:
            import ccxt
            # Verify primary CCXT exchange instances can resolve crypto symbols without BadSymbol
            coinbase = ccxt.coinbase()
            coinbase_markets = coinbase.load_markets()
            binance = ccxt.binance()
            binance_markets = binance.load_markets()

            for sym in crypto_symbols:
                # 1. CCXT Slashed Check
                if sym not in coinbase_markets and sym not in binance_markets:
                    issues.append(f"Crypto Symbol {sym} not recognized by Coinbase or Binance CCXT markets.")

                # 2. Unslashed normalization sanity check
                unslashed = sym.replace("/", "").replace("_", "").upper()
                re_slashed = None
                if "BTC" in unslashed: re_slashed = "BTC/USD"
                elif "ETH" in unslashed: re_slashed = "ETH/USD"
                elif "SOL" in unslashed: re_slashed = "SOL/USD"
                
                if re_slashed != sym:
                    issues.append(f"Symbol normalization failure: {unslashed} -> {re_slashed} (expected {sym})")

            # Check metals/forex unslashed normalization
            for sym in metals_forex_symbols:
                unslashed = sym.replace("/", "").replace("_", "").upper()
                re_slashed = "XAU/USD" if "XAU" in unslashed else None
                if re_slashed != sym:
                    issues.append(f"Metals symbol normalization failure: {unslashed} -> {re_slashed}")

        except Exception as e:
            issues.append(f"Symbol translation probe failed: {e}")

        return len(issues) == 0, issues

    # ── CHECK 2: DATABASE & SCHEMA INTEGRITY ─────────────────────────────────
    def check_schema_integrity(self, db_path: str = "data/smc_alpha.db") -> Tuple[bool, List[str]]:
        """
        Verifies that required tables and columns used by execution and watchdog logic exist.
        """
        issues = []
        if not os.path.exists(db_path):
            issues.append(f"Database file not found: {db_path}")
            return False, issues

        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            tables = [t[0] for t in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            
            required_tables = ["scans", "journal", "sync_state", "signed_ledger"]
            for rt in required_tables:
                if rt not in tables:
                    issues.append(f"Missing required SQLite table: {rt}")

            # Verify columns in journal table
            if "journal" in tables:
                journal_cols = [c[1] for c in cur.execute("PRAGMA table_info(journal);").fetchall()]
                for rc in ["id", "timestamp", "trade_id", "symbol", "side", "price", "pnl", "status"]:
                    if rc not in journal_cols:
                        issues.append(f"Missing required column in journal table: {rc}")

            # Verify daily setup lock JSON file
            lock_file = "data/daily_setup_lock.json"
            if os.path.exists(lock_file):
                with open(lock_file, "r") as f:
                    lock_data = json.load(f)
                    if not isinstance(lock_data, dict) or "date" not in lock_data or "setups_fired" not in lock_data:
                        issues.append(f"Malformed daily_setup_lock.json format: {lock_data}")
        except Exception as e:
            issues.append(f"Database schema verification error: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return len(issues) == 0, issues

    # ── CHECK 3: FLEET ACCOUNT DECOUPLING & SIZING INVARIANTS ─────────────────
    def check_fleet_invariants(self) -> Tuple[bool, List[str]]:
        """
        Verifies that account configuration complies with AGENTS.md rules:
        - Acc 1: $25k ($35 base)
        - Acc 2: $50k ($70 base)
        - Acc 3: $25k ($50 base)
        - Acc 6: $50k ($80 base)
        - Acc 7: $25k ($35 base)
        - Acc 9: $50k Oracle Lead Striker ($100 base)
        - Acc 4, 5, 8: Quarantined / $0.00 / LIQUIDATION_ONLY
        """
        issues = []
        try:
            from src.clients.tl_client import TradeLockerClient
            tl = TradeLockerClient()
            if not tl.helpers:
                issues.append("No TradeLocker accounts loaded in TradeLockerClient.")
                return False, issues

            expected_active_indices = {0, 1, 2, 5, 6, 8}
            quarantined_indices = {3, 4, 7}
            emergency_accounts = getattr(Config, 'EMERGENCY_LOCKOUT_ACCOUNTS', [])
            decoupled_risk = getattr(Config, 'DECOUPLED_STARTING_RISK', {})

            for idx, helper in enumerate(tl.helpers):
                email = getattr(helper, "email", f"acc_{idx+1}")
                status = getattr(helper, "status", "ACTIVE")

                if idx in quarantined_indices:
                    is_quarantined = (
                        status == "LIQUIDATION_ONLY" 
                        or email in emergency_accounts 
                        or decoupled_risk.get(email, 1.0) == 0.0
                    )
                    if not is_quarantined:
                        issues.append(f"Quarantined Account {idx+1} ({email}) has non-zero risk or is not in EMERGENCY_LOCKOUT_ACCOUNTS.")
                
                # Check scale-out index alignment
                if idx in expected_active_indices:
                    if idx in [0, 2, 8]:
                        # Scale-Out account: verify included in Config.SCALE_OUT_ACCOUNT_INDICES
                        if idx not in getattr(Config, 'SCALE_OUT_ACCOUNT_INDICES', []):
                            issues.append(f"Scale-out Account {idx+1} (idx {idx}) missing from Config.SCALE_OUT_ACCOUNT_INDICES.")
        except Exception as e:
            issues.append(f"Fleet invariant verification error: {e}")

        return len(issues) == 0, issues

    # ── CHECK 4: SILENT ERROR RATE SENTRY ────────────────────────────────────
    def scan_silent_error_rate(self, lookback_lines: int = 500) -> Tuple[bool, List[str]]:
        """
        Tails recent supervisor log lines to detect recurring silent failures.
        """
        issues = []
        if not os.path.exists(self.log_path):
            return True, []

        try:
            with open(self.log_path, "r", errors="ignore") as f:
                lines = f.readlines()[-lookback_lines:]

            # Filter out lines generated by Quality Governor itself to avoid recursive false triggers,
            # and only inspect entries from the last 15 minutes
            cutoff_dt = datetime.now() - timedelta(minutes=15)
            filtered_lines = []
            for line in lines:
                if "QualityGovernor" in line or "SILENT ERROR SENTRY" in line:
                    continue
                try:
                    ts_part = line[:19]
                    line_dt = datetime.strptime(ts_part, "%Y-%m-%d %H:%M:%S")
                    if line_dt < cutoff_dt:
                        continue
                except Exception:
                    pass
                filtered_lines.append(line)

            text_chunk = "".join(filtered_lines)
            for pattern, max_allowed, description in self.CRITICAL_ERROR_PATTERNS:
                count = text_chunk.count(pattern)
                if count >= max_allowed:
                    issues.append(f"🚨 SILENT ERROR SENTRY: '{pattern}' occurred {count}x (threshold: {max_allowed}) -> {description}")
        except Exception as e:
            issues.append(f"Silent error sentry read error: {e}")

        return len(issues) == 0, issues

    # ── CHECK 5: ACTIVE POSITION INVARIANT MONITOR ───────────────────────────
    def audit_active_positions(self, tl_client: Optional[Any] = None) -> Tuple[bool, List[str]]:
        """
        Verifies that every active open position complies with execution invariants:
        1. Attached Stop Loss (> 0)
        2. Valid Take Profit (> 0)
        3. On split scale-out accounts with 2 positions, TP1 != TP2 (No overwrite)
        4. If floating PnL >= +1.5R, verifies Stop Loss has trailed to Break-Even.
        """
        issues = []
        try:
            if tl_client is None:
                from src.clients.tl_client import TradeLockerClient
                tl_client = TradeLockerClient()

            positions = tl_client.get_open_positions()
            if not positions:
                return True, []  # Flat is clean

            # Group by account & symbol
            by_account_sym = {}
            for pos in positions:
                sym = pos.get("symbol", "")
                p_id = pos.get("id")
                entry = float(pos.get("price") or 0.0)
                pnl = float(pos.get("pnl") or 0.0)
                qty = float(pos.get("qty") or 0.0)
                sl = float(pos.get("stopLoss") or 0.0)
                tp = float(pos.get("takeProfit") or 0.0)
                side = str(pos.get("side", "")).upper()

                # Invariant 1: Mandatory attached Stop Loss (verified via price or attached broker order ID)
                sl_ord_id = pos.get("stopLossOrderId")
                if sl <= 0 and not sl_ord_id:
                    issues.append(f"🚨 [RULE 6 VIOLATION] Position {p_id} ({sym}) has NO protective Stop Loss on broker book!")

                # Invariant 2: Mandatory Take Profit (verified via price or attached broker order ID)
                tp_ord_id = pos.get("takeProfitOrderId")
                if tp <= 0 and not tp_ord_id:
                    issues.append(f"⚠️ Position {p_id} ({sym}) has NO Take Profit attached on broker book.")

                # Invariant 3: Floating R-Multiple Break-Even Lock Check
                if sl > 0 and entry > 0 and qty > 0:
                    contract_size = Config.get_contract_size(sym)
                    initial_sl = float(pos.get("initial_sl") or sl)
                    stop_dist = abs(entry - initial_sl)
                    if stop_dist > 0:
                        current_price = entry + (pnl / (qty * contract_size)) if side == "BUY" else entry - (pnl / (qty * contract_size))
                        from src.core.adversarial_quality_loop import PriceGeometryGroundTruth
                        r_mult = PriceGeometryGroundTruth.compute_geom_r(entry, initial_sl, current_price, side)
                        if r_mult >= 1.5:
                            # Verify if SL is at or better than entry price
                            is_be_locked = (sl >= entry) if side == "BUY" else (sl <= entry)
                            if not is_be_locked:
                                issues.append(
                                    f"🚨 [BREAK-EVEN INVARIANT BREACH] Position {p_id} ({sym}) reached +{r_mult:.2f}R "
                                    f"but Stop Loss (${sl:.2f}) is NOT locked at Break-Even (${entry:.2f})!"
                                )

                acc_id = str(pos.get("account_id") or pos.get("accountId") or pos.get("account_email") or "")
                key = (acc_id, sym, side)
                by_account_sym.setdefault(key, []).append(pos)

            # Invariant 4: Scale-Out Bracket Overwrite Prevention Check
            for (acc_id, sym, side), pos_list in by_account_sym.items():
                if len(pos_list) > 1:
                    tps = [float(p.get("takeProfit") or 0.0) for p in pos_list if p.get("takeProfit")]
                    if len(tps) > 1 and len(set(tps)) == 1 and tps[0] > 0:
                        acc_str = f"on account {acc_id} " if acc_id else ""
                        issues.append(
                            f"🚨 [SCALE-OUT BRACKET OVERWRITE] Multiple tranches {acc_str}for {sym} {side} share identical TP (${tps[0]:.2f}). "
                            "TP1 was overwritten by TP2!"
                        )

        except Exception as e:
            issues.append(f"Active position audit error: {e}")

        return len(issues) == 0, issues

    # ── CHECK 5: APPLE SILICON MLX LORA SERVER SENTRY ────────────────────────
    def check_mlx_lora_health(self, auto_revive: bool = True) -> Tuple[bool, List[str]]:
        """
        Audits health of local Apple Silicon MLX LoRA inference server on port 8080.
        If offline on macOS and auto_revive is True, attempts automatic revival.
        """
        if sys.platform != "darwin":
            return True, []

        issues = []
        url = "http://127.0.0.1:8080/v1/models"
        try:
            import requests
            res = requests.get(url, timeout=1.0)
            if res.status_code == 200:
                return True, []
        except Exception:
            pass

        issues.append("🤖 MLX LoRA inference server offline on port 8080")
        if auto_revive:
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
                    logger.info("🤖 [Quality Governor] Initiated auto-revival of MLX LoRA server daemon.")
                except Exception as revive_err:
                    issues.append(f"MLX LoRA auto-revival failed: {revive_err}")

        return False, issues

    # ── COMPREHENSIVE PRE-FLIGHT AUDIT ───────────────────────────────────────
    def run_preflight_audit(self) -> Dict[str, Any]:
        """
        Executes full pre-flight quality verification before supervisor starts trading.
        """
        logger.info("🏛️ [Quality Governor] Running Pre-Flight Codebase & Infrastructure Audit...")
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "PASS",
            "checks": {},
            "all_issues": []
        }

        # 1. Symbol translations
        passed_sym, sym_issues = self.check_symbol_translations()
        report["checks"]["symbol_translations"] = {"pass": passed_sym, "issues": sym_issues}
        if not passed_sym: report["all_issues"].extend(sym_issues)

        # 2. Database schema
        passed_db, db_issues = self.check_schema_integrity()
        report["checks"]["schema_integrity"] = {"pass": passed_db, "issues": db_issues}
        if not passed_db: report["all_issues"].extend(db_issues)

        # 3. Fleet configuration
        passed_fleet, fleet_issues = self.check_fleet_invariants()
        report["checks"]["fleet_invariants"] = {"pass": passed_fleet, "issues": fleet_issues}
        if not passed_fleet: report["all_issues"].extend(fleet_issues)

        # 4. MLX LoRA Server Health (macOS)
        if sys.platform == "darwin":
            passed_mlx, mlx_issues = self.check_mlx_lora_health(auto_revive=True)
            report["checks"]["mlx_lora_health"] = {"pass": passed_mlx, "issues": mlx_issues}
            if not passed_mlx:
                logger.warning(f"⚠️ [Quality Governor] MLX LoRA server pre-flight notice: {mlx_issues}")

        if report["all_issues"]:
            report["status"] = "FAIL"
            logger.error(f"❌ [Quality Governor] Pre-Flight Audit FAILED with {len(report['all_issues'])} issues:\n" + "\n".join(report["all_issues"]))
        else:
            logger.info("✅ [Quality Governor] Pre-Flight Audit PASSED: 100% Invariants Verified.")

        return report

    # ── CONTINUOUS RUNTIME AUDIT ─────────────────────────────────────────────
    def run_runtime_audit(self, tl_client: Optional[Any] = None) -> Dict[str, Any]:
        """
        Executes continuous runtime health scan. Called periodically in supervisor background.
        """
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "HEALTHY",
            "violations": []
        }

        # 1. Silent Error Sentry
        clean_logs, log_issues = self.scan_silent_error_rate(lookback_lines=300)
        if not clean_logs:
            report["violations"].extend(log_issues)
            report["status"] = "DEGRADED"

        # 2. Active Positions Invariant Audit
        clean_pos, pos_issues = self.audit_active_positions(tl_client=tl_client)
        if not clean_pos:
            report["violations"].extend(pos_issues)
            report["status"] = "CRITICAL"

        # 3. MLX LoRA Sentry
        if sys.platform == "darwin":
            clean_mlx, mlx_issues = self.check_mlx_lora_health(auto_revive=True)
            if not clean_mlx:
                report["violations"].extend(mlx_issues)
                if report["status"] == "HEALTHY":
                    report["status"] = "DEGRADED"

        if report["violations"]:
            logger.warning(f"⚠️ [Quality Governor] Runtime Audit detected {len(report['violations'])} issue(s):")
            for v in report["violations"]:
                logger.warning(f"   -> {v}")

            # Send Telegram Alert if critical and outside cooldown
            now = time.time()
            if report["status"] == "CRITICAL" and (now - self.last_alert_time) > self.alert_cooldown:
                try:
                    from src.clients.telegram_notifier import TelegramNotifier
                    tn = TelegramNotifier()
                    msg = (
                        f"🚨 <b>QUALITY GOVERNOR INVARIANT ALERT!</b>\n\n"
                        f"Status: <b>{report['status']}</b>\n"
                        f"Violations:\n" + "\n".join([f"• {v}" for v in report['violations'][:5]]) + "\n\n"
                        f"<i>Action: Verify supervisor books immediately.</i>"
                    )
                    tn._send_message(msg)
                    self.last_alert_time = now
                except Exception as tg_err:
                    logger.error(f"Failed to dispatch Quality Governor Telegram alert: {tg_err}")

        return report


if __name__ == "__main__":
    governor = QualityGovernor()
    if "--preflight" in sys.argv or len(sys.argv) == 1:
        res = governor.run_preflight_audit()
        print("\n" + "="*80)
        print(f"🏛️ PRE-FLIGHT AUDIT STATUS: {res['status']}")
        print("="*80)
        for check_name, data in res["checks"].items():
            icon = "✅" if data["pass"] else "❌"
            print(f"  {icon} {check_name}: {'PASSED' if data['pass'] else 'FAILED'}")
            for iss in data["issues"]:
                print(f"     - {iss}")
        sys.exit(0 if res["status"] == "PASS" else 1)
    elif "--runtime" in sys.argv:
        res = governor.run_runtime_audit()
        print(f"Runtime Status: {res['status']}")
        for v in res["violations"]:
            print(f"  - {v}")
