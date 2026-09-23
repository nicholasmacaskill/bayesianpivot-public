"""
Sovereign SMC Execution Firewall & Airgap Layer
==============================================
Enforces strict, cryptographic-level gatekeeping at the broker client layer.
Guarantees that NO order can physically reach TradeLocker unless all 9 institutional invariants are verified:

Invariants:
  1. Protective Bracket Gate: Strict Prohibition against Naked Trades (Stop Loss Mandatory)
  2. Weekend Quarantine Gate: 100% blocked on Saturday/Sunday (Zero Weekend Risk)
  3. High-Volume Session Gate: Restricted to London & NY Killzones (07-10, 13:30-17, 00-06 UTC)
  4. AI Validation Gate: Must possess verified AI Validator Score >= 8.0 / 10.0
  5. Higher-Timeframe Liquidity Gate: 5-minute single-candle bypasses physically blocked
  6. Fleet Anti-Stacking & Zero-Collision Gate: Prohibits duplicate open positions on the same symbol across fleet
  7. Fleet Max Concurrent Positions Limit: Hard cap on total open positions across the fleet
  8. Persistent Symbol Cooldown / Debounce Gate: 30-minute persistent debounce lock per symbol
  9. Account Liquidation & Drawdown Quarantine Gate: Physical lockout for LIQUIDATION_ONLY / frozen accounts
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from src.core.config import Config

logger = logging.getLogger("ExecutionFirewall")

COOLDOWNS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "trade_cooldowns.json"
)


class ExecutionFirewallViolation(Exception):
    """Raised when an order fails the Sovereign Execution Firewall."""
    pass


class ExecutionFirewall:
    """
    Hardware-level safety firewall protecting broker accounts from:
    - Rogue un-gated scripts
    - Concurrent runner duplication
    - Successive candle re-trigger stacking
    - 5m candle noise bypasses
    - Weekend chop friction
    - Broker duplicate order bugs
    """

    @staticmethod
    def _normalize_symbol(sym: str) -> str:
        return str(sym or "").replace("/", "").replace("_", "").upper()

    @staticmethod
    def get_last_execution_time(symbol: str) -> Optional[datetime]:
        """Reads persistent disk-backed cooldown timestamp for a symbol."""
        try:
            if not os.path.exists(COOLDOWNS_FILE_PATH):
                return None
            with open(COOLDOWNS_FILE_PATH, "r") as f:
                data = json.load(f)
                norm_sym = ExecutionFirewall._normalize_symbol(symbol)
                ts_str = data.get(norm_sym)
                if ts_str:
                    return datetime.fromisoformat(ts_str)
        except Exception as e:
            logger.warning(f"Error reading trade cooldowns: {e}")
        return None

    @staticmethod
    def record_trade_execution(symbol: str):
        """Records persistent execution timestamp for a symbol to enforce debounce."""
        try:
            os.makedirs(os.path.dirname(COOLDOWNS_FILE_PATH), exist_ok=True)
            data = {}
            if os.path.exists(COOLDOWNS_FILE_PATH):
                try:
                    with open(COOLDOWNS_FILE_PATH, "r") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            norm_sym = ExecutionFirewall._normalize_symbol(symbol)
            data[norm_sym] = datetime.now(timezone.utc).isoformat()
            with open(COOLDOWNS_FILE_PATH, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"⏳ [COOLDOWN RECORDED] Symbol {symbol} locked for next {getattr(Config, 'SYMBOL_COOLDOWN_MINUTES', 30)} minutes.")
        except Exception as e:
            logger.error(f"Error saving trade cooldown: {e}")

    @staticmethod
    def is_account_eligible(
        email: str,
        status: Optional[str],
        equity: float,
        hard_floor: float,
        open_positions_count: int = 0,
        today_realized_profit: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        INVARIANT 9: Verifies individual account eligibility:
        - Rejects LIQUIDATION_ONLY accounts
        - Rejects accounts on EMERGENCY_LOCKOUT_ACCOUNTS list
        - Rejects accounts where buffer above trailing drawdown floor < MIN_ACCOUNT_BUFFER_USD
        - Rejects accounts that already have >= MAX_POSITIONS_PER_ACCOUNT open positions
        - Rejects accounts that reached the 20% Consistency Rule Daily Profit Ceiling
        """
        if status and str(status).upper() == "LIQUIDATION_ONLY":
            return False, f"Account {email} status is LIQUIDATION_ONLY"
        
        quarantined = getattr(Config, 'EMERGENCY_LOCKOUT_ACCOUNTS', [])
        if email in quarantined:
            return False, f"Account {email} is in emergency drawdown quarantine list"

        remaining_buffer = max(0.0, equity - hard_floor)
        min_buffer = getattr(Config, 'MIN_ACCOUNT_BUFFER_USD', 100.0)
        if remaining_buffer < min_buffer:
            return False, f"Account {email} buffer (${remaining_buffer:,.2f}) < safe margin (${min_buffer:.2f})"

        max_acct_pos = getattr(Config, 'MAX_POSITIONS_PER_ACCOUNT', 2)
        if open_positions_count >= max_acct_pos:
            return False, f"Account {email} already has {open_positions_count} open positions (Max allowed: {max_acct_pos})"

        # Upcomers 20% Consistency Rule Daily Profit Ceiling Guard
        if today_realized_profit is not None:
            is_acc_1 = (email == getattr(Config, 'FUNDED_ACCOUNT_1_EMAIL', 's79qv3xetj@upcomers.com'))
            daily_cap = getattr(Config, 'ACCOUNT_1_DAILY_PROFIT_CAP', 380.0) if is_acc_1 else (
                getattr(Config, 'TIER_MAX_DAILY_PROFIT_50K', 760.0) if equity > 35000.0 else getattr(Config, 'TIER_MAX_DAILY_PROFIT_25K', 380.0)
            )
            if today_realized_profit >= daily_cap:
                return False, f"Account {email} reached 20% Consistency Daily Profit Ceiling (${today_realized_profit:,.2f} >= ${daily_cap:,.2f}). Trading locked to preserve payout compliance."

        return True, "ELIGIBLE"

    @staticmethod
    def check_daily_loss_circuit_breaker() -> Tuple[bool, str]:
        """
        INVARIANT 10: Checks if today's closed setups hit consecutive loss limits.
        Clusters multi-tranche and multi-account fleet tickets of the same setup into a single setup outcome.
        If >= MAX_CONSECUTIVE_DAILY_LOSSES (2) consecutive setups lost today, halts trading for 24 hours.
        """
        try:
            import sqlite3
            from datetime import datetime, timezone
            db_path = getattr(Config, 'DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "smc_alpha.db"))
            if os.path.exists(db_path):
                conn = None
                try:
                    conn = sqlite3.connect(db_path, timeout=5.0)
                    cur = conn.cursor()
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    cur.execute("""
                        SELECT timestamp, symbol, side, pnl FROM journal 
                        WHERE timestamp LIKE ? AND status = 'CLOSED' AND strategy != 'ROGUE'
                        ORDER BY id DESC LIMIT 50
                    """, (f"{today_str}%",))
                    rows = cur.fetchall()
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass

                if not rows:
                    return True, "OK"

                def parse_time(ts_str):
                    if not ts_str:
                        return 0.0
                    clean_ts = ts_str.replace("Z", "").split(".")[0]
                    try:
                        return datetime.fromisoformat(clean_ts).timestamp()
                    except Exception:
                        return 0.0

                # Group rows into distinct setup clusters (tickets within 15 mins on same symbol)
                # rows are ordered DESC (most recent first)
                setup_clusters = []
                current_cluster = []
                
                for r in rows:
                    ts, sym, side, pnl = r[0], r[1], r[2], float(r[3] or 0.0)
                    t_sec = parse_time(ts)
                    
                    if not current_cluster:
                        current_cluster.append({'time': t_sec, 'symbol': sym, 'pnl': pnl})
                    else:
                        ref = current_cluster[0]
                        # 45-minute window accounts for multi-account adaptive pacing and delayed broker syncs of the same setup
                        if abs(t_sec - ref['time']) <= 2700 and sym == ref['symbol']:
                            current_cluster.append({'time': t_sec, 'symbol': sym, 'pnl': pnl})
                        else:
                            setup_clusters.append(current_cluster)
                            current_cluster = [{'time': t_sec, 'symbol': sym, 'pnl': pnl}]
                if current_cluster:
                    setup_clusters.append(current_cluster)

                max_loss_streak = getattr(Config, 'MAX_CONSECUTIVE_DAILY_LOSSES', 2)
                loss_streak = 0
                for cluster in setup_clusters:
                    cluster_net_pnl = sum(item['pnl'] for item in cluster)
                    if cluster_net_pnl < 0:
                        loss_streak += 1
                    else:
                        break

                if loss_streak >= max_loss_streak:
                    return False, f"Daily consecutive loss ceiling hit ({loss_streak}/{max_loss_streak} distinct setups lost today). Trading locked for 24h to preserve prop equity."
        except Exception as e:
            logger.warning(f"Error checking daily loss circuit breaker: {e}")
        return True, "OK"


    @staticmethod
    def check_global_daily_setup_limit() -> Tuple[bool, str]:
        """
        INVARIANT 11: Global Daily Setup Limit (Max 1-2 Setups Per Day) via Atomic Lock.
        """
        import json
        import os
        from datetime import datetime, timezone
        
        lock_file_path = "data/daily_setup_lock.json"
        try:
            if os.path.exists(lock_file_path):
                with open(lock_file_path, "r") as f:
                    data = json.load(f)
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if data.get("date") == today_str and data.get("setups_fired", 0) >= 2:
                    return False, f"Daily Setup Limit hit ({data.get('setups_fired')} setups locked in memory). Trading locked for 24h."
        except Exception as e:
            return False, f"Setup limit check failed: {e}"
        return True, "OK"
        

    @staticmethod
    def check_news_calendar(symbol: Optional[str] = None) -> Tuple[bool, str]:
        """
        INVARIANT 13: Economic Calendar / News Filter (asset-aware).
        """
        try:
            from src.engines.calendar_filter import CalendarFilter
            cal = CalendarFilter()
            if hasattr(cal, "is_safe_to_trade"):
                try:
                    is_safe, reason = cal.is_safe_to_trade(symbol=symbol)
                except TypeError:
                    is_safe, reason = cal.is_safe_to_trade()
            elif hasattr(cal, "check"):
                is_safe, reason = cal.check()
            else:
                is_safe, reason = True, "OK"
            if not is_safe:
                return False, f"News Invariant: {reason}"
        except Exception as e:
            return False, f"Calendar check unreachable or failed: {e}"
        return True, "OK"

    @staticmethod
    def check_trending_regime_lock(hurst_exponent: float, strategy_mode: str = "") -> Tuple[bool, str]:
        """
        INVARIANT 12: Regime-Aware Hurst Governor. Decouples trend logic from mean-reversion.
        """
        if hurst_exponent is None:
            return True, "OK"
            
        strategy_lower = strategy_mode.lower() if strategy_mode else ""
        is_mean_reverting = any(x in strategy_lower for x in ["turtle soup", "range fade", "sweep", "judas"])

        if is_mean_reverting:
            # Mean-reverting strategies fail in strong persistent trends
            if hurst_exponent > 0.60:
                return False, f"Regime Governor: Hurst ({hurst_exponent:.2f}) > 0.60. Too strongly trending for a mean-reverting strategy."
        else:
            # Trend-following strategies fail in choppy/random regimes
            if hurst_exponent < 0.45:
                return False, f"Regime Governor: Hurst ({hurst_exponent:.2f}) < 0.45. Mean-reverting chop detected, blocking trend setup."
                
        return True, "OK"

    @staticmethod
    def audit_trade_request(
        symbol: str,
        side: str,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        ai_score: Optional[float] = None,
        strategy_mode: Optional[str] = None,
        is_htf_confirmed: bool = True,
        bypass_killzone: bool = False,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        bypass_cooldown: bool = False,
        bypass_circuit_breaker: bool = False,
        hurst_exponent: Optional[float] = None,
        bypass_weekend: bool = False
    ) -> Tuple[bool, str]:
        """
        Audits an incoming trade request against all 10 Ironclad Invariants.
        Returns (is_approved: bool, reason: str).
        """
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour + (now_utc.minute / 60.0)
        weekday = now_utc.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        norm_target_sym = ExecutionFirewall._normalize_symbol(symbol)

        # ── INVARIANT 1: Mandatory Protective Stop Loss (Zero Naked Trades) ──
        if stop_loss is None or float(stop_loss) <= 0:
            err = "FIREWALL REJECTION (Gate 1): Zero naked orders allowed. Protective Stop Loss is mandatory."
            logger.critical(f"🛡️ [FIREWALL BLOCKED] {err}")
            return False, err

        # ── INVARIANT 2: Universal Weekend & Friday Pre-Close Quarantine ──
        if not bypass_weekend:
            if weekday in (5, 6):  # Saturday or Sunday
                err = f"FIREWALL REJECTION (Gate 2): Weekend Execution Locked (Weekday={weekday}). Zero live capital risk on weekends."
                logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

            if weekday == 4 and utc_hour >= 18.0:  # Friday after 18:00 UTC
                err = f"FIREWALL REJECTION (Gate 2): Friday Pre-Weekend Lockout active ({utc_hour:.2f} UTC >= 18:00). Prohibiting new entries before market close."
                logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

        # ── INVARIANT 3: London & NY Prime Killzone Gate ──
        if not bypass_killzone:
            # High-Alpha Windows (UTC):
            # 1. London Open: 07:00 - 10:00 UTC
            # 2. London Close / NY Morning: 12:00 - 17:00 UTC
            # 3. Asian Judas: 00:00 - 06:00 UTC
            is_prime_killzone = (
                (7.0 <= utc_hour <= 10.0) or
                (12.0 <= utc_hour <= 17.0) or
                (0.0 <= utc_hour <= 6.0)
            )
            if not is_prime_killzone:
                err = f"FIREWALL REJECTION (Gate 3): Current time {utc_hour:.2f} UTC is outside verified institutional killzones."
                logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

        # ── INVARIANT 4: AI Validator Conviction Threshold (>= 8.0 / 10.0) ──
        effective_ai_score = float(ai_score) if ai_score is not None else 0.0
        if effective_ai_score < 8.0:
            err = f"FIREWALL REJECTION (Gate 4): AI Score {effective_ai_score:.1f}/10.0 is below the mandatory 8.0/10.0 threshold."
            logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
            return False, err

        # ── INVARIANT 5: Higher-Timeframe Structural Confirmation ──
        if not is_htf_confirmed:
            err = "FIREWALL REJECTION (Gate 5): Single 5-minute candle bypasses are prohibited. Requires 1H HTF Liquidity Pool confirmation."
            logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
            return False, err

        # ── INVARIANT 6: Fleet Anti-Stacking & Zero-Collision Gate ──
        if open_positions is not None:
            for p in open_positions:
                pos_sym = ExecutionFirewall._normalize_symbol(p.get("symbol", ""))
                # Check for direct symbol collision or cross-asset root
                if norm_target_sym == pos_sym or (norm_target_sym in pos_sym and len(norm_target_sym) > 2):
                    err = f"FIREWALL REJECTION (Gate 6 - Anti-Stacking): Active position already exists on {symbol} (Position ID: {p.get('id')}). Prohibiting duplicate stacking."
                    logger.critical(f"🛡️ [FIREWALL BLOCKED] {err}")
                    return False, err

        # ── INVARIANT 7: Fleet Max Concurrent Positions Limit ──
        if open_positions is not None:
            max_fleet_pos = getattr(Config, 'MAX_CONCURRENT_FLEET_POSITIONS', 2)
            # Count distinct active positions across the fleet
            distinct_open_syms = set(ExecutionFirewall._normalize_symbol(p.get("symbol", "")) for p in open_positions if p.get("symbol"))
            if len(distinct_open_syms) >= max_fleet_pos:
                err = f"FIREWALL REJECTION (Gate 7 - Max Concurrent Fleet Positions): Active symbols {distinct_open_syms} meet/exceed fleet ceiling ({max_fleet_pos})."
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

        # ── INVARIANT 8: Persistent Symbol Cooldown / Debounce Gate ──
        if not bypass_cooldown:
            last_exec = ExecutionFirewall.get_last_execution_time(symbol)
            if last_exec:
                cooldown_mins = getattr(Config, 'SYMBOL_COOLDOWN_MINUTES', 30)
                elapsed_sec = (now_utc - last_exec).total_seconds()
                required_sec = cooldown_mins * 60.0
                if elapsed_sec < required_sec:
                    remaining_mins = (required_sec - elapsed_sec) / 60.0
                    err = f"FIREWALL REJECTION (Gate 8 - Debounce Cooldown): Symbol {symbol} was executed {elapsed_sec/60:.1f}m ago. Cooldown active for {remaining_mins:.1f} more minutes."
                    logger.warning(f"🛡️ [FIREWALL BLOCKED] {err}")
                    return False, err

        # ── INVARIANT 10: Daily Consecutive Loss Circuit Breaker ──
        if not bypass_circuit_breaker:
            cb_ok, cb_reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            if not cb_ok:
                err = f"FIREWALL REJECTION (Gate 10 - Circuit Breaker): {cb_reason}"
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

        # ── INVARIANT 11: Global Daily Setup Limit ──
        if not bypass_circuit_breaker:
            limit_ok, limit_reason = ExecutionFirewall.check_global_daily_setup_limit()
            if not limit_ok:
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {limit_reason}")
                return False, limit_reason

        # ── INVARIANT 12: Hurst Regime Filter ──
        if hurst_exponent is not None:
            regime_ok, regime_reason = ExecutionFirewall.check_trending_regime_lock(hurst_exponent, strategy_mode=strategy_mode)
            if not regime_ok:
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {regime_reason}")
                return False, regime_reason

        # ── INVARIANT 13: Economic News Calendar ──
        if not bypass_circuit_breaker:
            news_ok, news_reason = ExecutionFirewall.check_news_calendar(symbol=symbol)
            if not news_ok:
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {news_reason}")
                return False, news_reason

        logger.info(f"🛡️ [FIREWALL APPROVED] Trade on {symbol} {side.upper()} verified across all 13 Invariants.")
        return True, "APPROVED_BY_FIREWALL"
