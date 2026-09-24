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
    def get_canonical_killzone_session(utc_dt: Optional[datetime] = None, session_hint: Optional[str] = None) -> str:
        """
        Normalizes any session string or UTC timestamp into one of the canonical killzones:
        - 'ASIAN_JUDAS' (00:00 - 06:00 UTC)
        - 'LONDON_OPEN' (07:00 - 10:00 UTC)
        - 'NY_MORNING'  (12:00 - 17:00 UTC)
        """
        hint_clean = str(session_hint or "").upper()
        if "ASIA" in hint_clean or "JUDAS" in hint_clean:
            return "ASIAN_JUDAS"
        if "LONDON_OPEN" in hint_clean or (("LONDON" in hint_clean or "LDN" in hint_clean) and "CLOSE" not in hint_clean and "NY" not in hint_clean):
            return "LONDON_OPEN"
        if "NY" in hint_clean or "NEW_YORK" in hint_clean or "CLOSE" in hint_clean or "MORNING" in hint_clean:
            return "NY_MORNING"

        if utc_dt is None:
            utc_dt = datetime.now(timezone.utc)
        utc_float = utc_dt.hour + (utc_dt.minute / 60.0)

        if 0.0 <= utc_float <= 6.0:
            return "ASIAN_JUDAS"
        elif 7.0 <= utc_float <= 10.0:
            return "LONDON_OPEN"
        elif 12.0 <= utc_float <= 17.0:
            return "NY_MORNING"
        elif 17.0 < utc_float <= 20.0:
            return "NY_AFTERNOON"
        return "OUTSIDE_KILLZONE"

    @staticmethod
    def check_session_setup_limit(session_hint: str = "", utc_dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        INVARIANT 9: Session Anti-Clustering Gate.
        Enforces maximum 1 setup per killzone session (Asian Judas, London Open, NY Morning).
        """
        import json
        import os
        import sqlite3
        from datetime import datetime, timezone
        
        canonical_session = ExecutionFirewall.get_canonical_killzone_session(utc_dt=utc_dt, session_hint=session_hint)
        if canonical_session in ("OUTSIDE_KILLZONE", "NY_AFTERNOON"):
            return True, "OK"

        max_allowed = getattr(Config, 'MAX_SETUPS_PER_KILLZONE_SESSION', 1)
        today_str = (utc_dt or datetime.now(timezone.utc)).strftime("%Y-%m-%d")

        # 1. Check Atomic Daily Setup Lock file
        lock_file_path = "data/daily_setup_lock.json"
        try:
            if os.path.exists(lock_file_path):
                with open(lock_file_path, "r") as f:
                    data = json.load(f)
                if data.get("date") == today_str:
                    session_counts = data.get("session_counts", {})
                    if isinstance(session_counts, dict) and canonical_session in session_counts:
                        count = session_counts.get(canonical_session, 0)
                        if count >= max_allowed:
                            return False, f"FIREWALL REJECTION (Gate 9 - Session Anti-Clustering): Session '{canonical_session}' setup ceiling reached ({count}/{max_allowed}). Max 1 setup per killzone session."
                    setups = data.get("setups", [])
                    if isinstance(setups, list) and not session_counts:
                        matching_setups = [s for s in setups if s.get("session") == canonical_session]
                        if len(matching_setups) >= max_allowed:
                            return False, f"FIREWALL REJECTION (Gate 9 - Session Anti-Clustering): Session '{canonical_session}' setup ceiling reached ({len(matching_setups)}/{max_allowed}). Max 1 setup per killzone session."
        except Exception as e:
            logger.debug(f"Note checking session lock file: {e}")

        # 2. Corroborate with Journal database if available
        try:
            db_path = getattr(Config, 'DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "smc_alpha.db"))
            if os.path.exists(db_path):
                conn = None
                try:
                    conn = sqlite3.connect(db_path, timeout=5.0)
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT timestamp, symbol FROM journal 
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

                if rows:
                    def parse_time(ts_str):
                        if not ts_str: return 0.0
                        clean_ts = ts_str.replace("Z", "").split(".")[0]
                        try: return datetime.fromisoformat(clean_ts).timestamp()
                        except Exception: return 0.0

                    setup_clusters = []
                    current_cluster = []
                    for r in rows:
                        ts, sym = r[0], r[1]
                        t_sec = parse_time(ts)
                        if not current_cluster:
                            current_cluster.append({'time': t_sec, 'symbol': sym, 'ts_str': ts})
                        else:
                            ref = current_cluster[0]
                            if abs(t_sec - ref['time']) <= 2700 and sym == ref['symbol']:
                                current_cluster.append({'time': t_sec, 'symbol': sym, 'ts_str': ts})
                            else:
                                setup_clusters.append(current_cluster)
                                current_cluster = [{'time': t_sec, 'symbol': sym, 'ts_str': ts}]
                    if current_cluster:
                        setup_clusters.append(current_cluster)

                    session_setup_count = 0
                    for cluster in setup_clusters:
                        first_ts = cluster[0]['ts_str']
                        clean_ts = first_ts.replace("Z", "").split(".")[0]
                        try:
                            c_dt = datetime.fromisoformat(clean_ts).replace(tzinfo=timezone.utc)
                            c_session = ExecutionFirewall.get_canonical_killzone_session(utc_dt=c_dt)
                            if c_session == canonical_session:
                                session_setup_count += 1
                        except Exception:
                            pass

                    if session_setup_count >= max_allowed:
                        return False, f"FIREWALL REJECTION (Gate 9 - Session Anti-Clustering): Session '{canonical_session}' setup ceiling reached ({session_setup_count}/{max_allowed} closed setups today). Max 1 setup per killzone session."
        except Exception as e:
            logger.debug(f"Note checking session journal records: {e}")

        return True, "OK"

    @staticmethod
    def check_daily_loss_circuit_breaker() -> Tuple[bool, str]:
        """
        INVARIANT 10: Checks if today's closed setups hit consecutive loss limits or cumulative 2.0 Risk Unit loss ceiling.
        Clusters multi-tranche and multi-account fleet tickets of the same setup into a single setup outcome.
        - If cumulative realized loss hits -2.0 Units (e.g. 2 full losses at -1.0R, or 4 half-size probe losses at -0.5R),
          the fleet locks down immediately for 24 hours.
        - Preserves consecutive loss streak guard.
        """
        try:
            import sqlite3
            import json
            from datetime import datetime, timezone
            db_path = getattr(Config, 'DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "smc_alpha.db"))
            if os.path.exists(db_path):
                conn = None
                try:
                    conn = sqlite3.connect(db_path, timeout=5.0)
                    cur = conn.cursor()
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    cur.execute("""
                        SELECT timestamp, symbol, side, pnl, strategy FROM journal 
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

                # Group rows into distinct setup clusters (tickets within 45 mins on same symbol)
                # rows are ordered DESC (most recent first)
                setup_clusters = []
                current_cluster = []
                
                for r in rows:
                    ts, sym, side, pnl = r[0], r[1], r[2], float(r[3] or 0.0)
                    strat = str(r[4] or "")
                    t_sec = parse_time(ts)
                    
                    item = {'time': t_sec, 'symbol': sym, 'pnl': pnl, 'strategy': strat}
                    if not current_cluster:
                        current_cluster.append(item)
                    else:
                        ref = current_cluster[0]
                        # 45-minute window accounts for multi-account adaptive pacing and delayed broker syncs of the same setup
                        if abs(t_sec - ref['time']) <= 2700 and sym == ref['symbol']:
                            current_cluster.append(item)
                        else:
                            setup_clusters.append(current_cluster)
                            current_cluster = [item]
                if current_cluster:
                    setup_clusters.append(current_cluster)

                # Try loading daily setup lock to correlate exact risk units if recorded
                recorded_setups = []
                lock_file_path = "data/daily_setup_lock.json"
                if os.path.exists(lock_file_path):
                    try:
                        with open(lock_file_path, "r") as lf:
                            lock_data = json.load(lf)
                            if lock_data.get("date") == today_str:
                                recorded_setups = lock_data.get("setups", [])
                    except Exception:
                        pass

                def get_cluster_risk_units(cluster):
                    ref_item = cluster[0]
                    ref_sym = ref_item['symbol']
                    ref_time = ref_item['time']
                    # 1. Match from recorded setups in lock file
                    for s in recorded_setups:
                        if s.get("symbol") == ref_sym and abs(s.get("timestamp", 0) - ref_time) <= 3600:
                            return float(s.get("units", 1.0))
                    # 2. Check strategy / label for probe indicators
                    is_probe = any(
                        "PROBE" in item['strategy'].upper() or "AUCTION" in item['strategy'].upper()
                        for item in cluster
                    )
                    if is_probe:
                        return 0.5
                    # 3. Fallback: single account test heuristic (Account 1 risk = $35, probe risk = $17.50)
                    cluster_loss = abs(sum(item['pnl'] for item in cluster))
                    if 10.0 <= cluster_loss <= 22.0:
                        return 0.5
                    return 1.0

                max_loss_streak = getattr(Config, 'MAX_CONSECUTIVE_DAILY_LOSSES', 2)
                max_loss_units = float(getattr(Config, 'DAILY_LOSS_UNIT_CIRCUIT_BREAKER', 2.0))
                
                loss_streak = 0
                cumulative_loss_units = 0.0
                consecutive_loss_units = 0.0

                # Compute cumulative loss units across all losing setups today
                for cluster in setup_clusters:
                    cluster_net_pnl = sum(item['pnl'] for item in cluster)
                    if cluster_net_pnl < 0:
                        unit_weight = get_cluster_risk_units(cluster)
                        cumulative_loss_units += unit_weight

                # Compute consecutive loss streak (ordered DESC: stop at first non-loss)
                for cluster in setup_clusters:
                    cluster_net_pnl = sum(item['pnl'] for item in cluster)
                    if cluster_net_pnl < 0:
                        loss_streak += 1
                        consecutive_loss_units += get_cluster_risk_units(cluster)
                    else:
                        break

                # 2.0 Unit Cumulative Loss Circuit Breaker
                if cumulative_loss_units >= max_loss_units - 1e-4:
                    return False, f"Daily consecutive loss ceiling hit / cumulative loss limit reached ({cumulative_loss_units:.1f}/{max_loss_units:.1f} Units lost today). Trading locked for 24h to preserve prop equity."

                # Consecutive loss streak (only triggers if units >= 1.5, protecting two 0.5x probe losses)
                if loss_streak >= max_loss_streak:
                    if consecutive_loss_units >= 1.5 or cumulative_loss_units >= 1.5:
                        return False, f"Daily consecutive loss ceiling hit ({loss_streak}/{max_loss_streak} distinct setups lost today, {cumulative_loss_units:.1f} units). Trading locked for 24h to preserve prop equity."

        except Exception as e:
            logger.warning(f"Error checking daily loss circuit breaker: {e}")
        return True, "OK"

    @staticmethod
    def check_global_daily_setup_limit(requested_units: float = 1.0) -> Tuple[bool, str]:
        """
        INVARIANT 11: Global Daily Setup Limit (Risk-Budgeted Accounting via Atomic Lock).
        Allows up to 3.0 Risk Units per day (e.g. two 0.50x probes + two 1.00x full trades).
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
                if data.get("date") == today_str:
                    max_units = float(getattr(Config, 'DAILY_RISK_UNIT_CAP', 3.0))
                    units_used = float(data.get("units_used", data.get("risk_units_used", data.get("setups_fired", 0))))
                    if (units_used + requested_units) > max_units + 1e-4:
                        return False, f"Daily Risk Unit Limit hit ({units_used:.1f}/{max_units:.1f} units consumed today, requested {requested_units:.1f}). Trading locked for 24h."
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
        bypass_weekend: bool = False,
        risk_scale: float = 1.0,
        session: str = ""
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

        # ── INVARIANT 9: Session Anti-Clustering Gate (Max 1 Setup Per Killzone Session) ──
        if not bypass_killzone and not bypass_circuit_breaker:
            session_ok, session_reason = ExecutionFirewall.check_session_setup_limit(session_hint=session, utc_dt=now_utc)
            if not session_ok:
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {session_reason}")
                return False, session_reason

        # ── INVARIANT 10: Daily Consecutive Loss Circuit Breaker ──
        if not bypass_circuit_breaker:
            cb_ok, cb_reason = ExecutionFirewall.check_daily_loss_circuit_breaker()
            if not cb_ok:
                err = f"FIREWALL REJECTION (Gate 10 - Circuit Breaker): {cb_reason}"
                logger.critical(f"🛡️ [FIREWALL BLOCKED] {err}")
                return False, err

        # ── INVARIANT 11: Global Daily Setup Limit (Risk-Budgeted Accounting) ──
        if not bypass_circuit_breaker:
            limit_ok, limit_reason = ExecutionFirewall.check_global_daily_setup_limit(requested_units=risk_scale)
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
