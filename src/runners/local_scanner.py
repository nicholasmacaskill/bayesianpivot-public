import matplotlib
matplotlib.use('Agg')
import time
import requests
import logging
import signal
import sys
import os
import fcntl
import multiprocessing
from datetime import datetime, timezone, timedelta

# Fix ModuleNotFoundError: No module named 'src' <!-- id: 16 -->
sys.path.append(os.getcwd())

# Prevent yfinance SQLite cache locking collisions
try:
    import yfinance as yf
    yf.set_tz_cache_location("/tmp/yfinance_cache")
except Exception:
    pass

# Fix macOS Multiprocessing Pickling Error (TypeError: cannot pickle 'weakref.ReferenceType')
if sys.platform == 'darwin':
    try:
        multiprocessing.set_start_method('fork', force=True)
    except RuntimeError:
        pass

from src.core.config import Config
from src.engines.smc_scanner import SMCScanner
from src.engines.sentiment_engine import SentimentEngine
from src.engines.ai_validator import validate_setup
from src.engines.visualizer import generate_ict_chart
from src.core.memory import memory
from src.engines.prop_guardian import PropGuardian
from src.core.database import init_db, log_scan, update_sync_state, log_system_event, get_db_connection, log_prop_audit
from src.clients.tl_client import TradeLockerClient
from src.clients.telegram_notifier import TelegramNotifier, send_alert, send_system_error
from src.engines.execution_audit import ExecutionAuditEngine
from src.engines.guard_engine import GuardEngine
from src.engines.local_llm_handler import LocalLLMHandler
# ── NEW: 5-Feature Suite ─────────────────────────────────────────────────────
from src.engines.correlation_gate import CorrelationGate
from src.engines.calendar_filter  import CalendarFilter
from src.engines.regime_filter     import RegimeFilter
from src.engines.trade_ledger      import TradeLedger
from src.engines.retraining_loop   import RetrainingLoop
from src.engines.psychology_engine import PsychologyEngine
from src.engines.biometric_engine  import BiometricEngine
from src.engines.news_catalyst_scanner import NewsCatalystScanner
from src.engines.multi_account_funnel import MultiAccountFunnelManager
from src.engines.counterfactual_tracker import CounterfactualTracker
from src.engines.qa_quant_agent import QAQuantAgent
from src.engines.inducement_tracker import InducementTracker
from src.engines.judas_inducement_engine import JudasInducementEngine




# ── ICT Killzone Definitions (UTC hours) ─────────────────────────────────────
ICT_KILLZONES = [
    {'name': 'Asian & Judas',         'open': 0,  'close': 6},
    {'name': 'London',                'open': 7,  'close': 10},
    {'name': 'London Close / NY AM',  'open': 13, 'close': 16},
    {'name': 'NY PM (Shadow Only)',   'open': 16, 'close': 20},
]

def _get_active_killzone(utc_hour: int) -> dict | None:
    """Returns the active ICT killzone dict for the given UTC hour, or None."""
    for kz in ICT_KILLZONES:
        if kz['open'] <= utc_hour < kz['close']:
            return kz
    return None

def _is_presession_window(utc_hour: int, utc_minute: int) -> dict | None:
    """Returns killzone dict if we are within 15 minutes before its open, else None."""
    for kz in ICT_KILLZONES:
        pre_hour   = (kz['open'] - 1) % 24
        pre_minute = 45
        if utc_hour == pre_hour and utc_minute >= pre_minute:
            return kz
        if utc_hour == kz['open'] and utc_minute < 5:  # First 5 mins counts too
            return kz
    return None

# ── Logging Setup ────────────────────────────────────────────────────────────
# SafeStreamHandler: swallows [Errno 5] Input/output error that occurs when
# the controlling terminal is detached (broken pipe on sys.stdout).
class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except (OSError, IOError):
            # Silently ignore broken-pipe / EIO from a disconnected terminal
            pass

from logging.handlers import RotatingFileHandler as _RotatingFileHandler
_file_handler = _RotatingFileHandler(
    "logs/local_runner.log",
    maxBytes=10 * 1024 * 1024,   # 10 MB per file
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

_stream_handler = SafeStreamHandler(sys.stdout)
_stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        _file_handler,
        _stream_handler,
    ]
)
# Silence Noisy Third-Party Loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("LocalRunner")

# Lock file to prevent duplicate processes
LOCK_FILE = os.path.join(os.getcwd(), "data", "smc_scanner.lock")

def check_single_instance():
    """Ensure only one instance of the scanner is running."""
    lock_file = open(LOCK_FILE, "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except IOError:
        print("⚠️  Another instance of Bayesian Pivot is already running. Exiting.")
        sys.exit(0)

class LocalScannerRunner:
    def __init__(self):
        self.lock = check_single_instance()
        self.scanner = SMCScanner()
        self.sentiment_engine = SentimentEngine()
        self.tl = TradeLockerClient()
        self.audit_engine = ExecutionAuditEngine()
        self.prop_guardian = PropGuardian()
        self.retrain_loop = RetrainingLoop()
        
        self.running = True
        self._cycle_count = 0
        self._last_signal_time = None
        self.processed_interviews = set()
        self.scan_results = {} # Track per-symbol state for summary
        
        # State indicators
        self.risk_multiplier = 1.0
        self.last_prop_audit = 0
        self.last_market_pulse = 0
        
        # ── 5-Feature Suite ───────────────────────────────────────────
        self.notifier = TelegramNotifier()
        self.corr_gate    = CorrelationGate(
            max_per_direction=Config.get('CORRELATION_MAX_PER_DIRECTION', 1),
            expiry_hours=Config.get('CORRELATION_SLOT_EXPIRY_HRS', 4),
        )
        self.cal_filter   = CalendarFilter(
            blackout_minutes=Config.get('CALENDAR_BLACKOUT_MINUTES', 30),
        )
        self.regime_filter = RegimeFilter()
        self.news_catalyst = NewsCatalystScanner()
        self.ledger        = TradeLedger() if Config.get('LEDGER_ENABLED', True) else None

        # ── Multi-Account Funnel Matrix, Counterfactual Tracker & QA Auditor ─────
        self.funnel_manager = MultiAccountFunnelManager()
        self.counterfactual_tracker = CounterfactualTracker()
        self.qa_agent = QAQuantAgent()
        self.inducement_tracker = InducementTracker()
        self.judas_engine = JudasInducementEngine()
        self.learned_weights = self._load_learned_weights()



        
        # ── Biometrics & Psychology ───────────────────────────────────
        self.psychology    = PsychologyEngine()
        self.biometrics    = BiometricEngine(port=8080)
        self.biometrics.start_server()
        self.current_tilt_score = 1
        self.risk_multiplier   = 1.0
        self.last_psych_state  = {"tilt_score": 1, "sentiment": "Neutral", "reasoning": "Standard Start"}
        self.awaiting_psych_response = False
        self.last_psych_prompt_time = 0
        
        # Discretionary Alpha Interview State
        self.awaiting_alpha_interview = False
        self.interview_trade_id = None
        self.last_interview_prompt_time = 0
        self.processed_interviews = set()
        self.last_command_time = int(time.time()) - 300
        self.session_start_time = int(time.time())
        self.last_session_name = None

        # ── Bayesian Pivot Guard (Security Layer) ───────────────────────────
        self.guard = GuardEngine(notifier=self.notifier)
        self.guard.start()
        logger.info("🛡️  Bayesian Pivot Guard active — securing your edge.")
        # ────────────────────────────────────────────────────────────────────

        # ── MLX LoRA Local Fallback Validator ─────────────────────────────────
        self.local_llm = LocalLLMHandler()
        if self.local_llm.is_available():
            logger.info("🤖 MLX LoRA local validator: ONLINE")
        else:
            logger.warning("🤖 MLX LoRA local validator: OFFLINE (Port 8080)")
        # ─────────────────────────────────────────────────────────────────────

        self.last_market_pulse = 0
        self._last_heartbeat_time = 0
        self._cycle_count = 0
        self._last_signal_time = None
        self._last_scan_results = [] # Track results for /scan report
        self._presession_scanned = set()  # Track which sessions have been pre-scanned this cycle

        # Shutdown handler
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def _load_learned_weights(self):
        """Loads empirical Bayesian strategy weights calibrated from historical replay."""
        try:
            path = "data/learned_strategy_weights.json"
            if os.path.exists(path):
                with open(path) as f:
                    weights = json.load(f)
                    logger.info(f"🧠 Loaded {len(weights)} Bayesian Strategy Weights from {path}")
                    return weights
        except Exception as e:
            logger.debug(f"Could not load learned weights: {e}")
        return {}

    def shutdown(self, signum, frame):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        self.running = False
        self.guard.stop()
        logger.info("🛡️  Bayesian Pivot Guard stopped.")

    def _handle_commands(self):
        """Listens for and executes Telegram commands (/status, /scan)."""
        msg = self.notifier.get_latest_message(since_timestamp=self.last_command_time)
        if not msg: 
            return
        
        self.last_command_time = msg['timestamp'] + 1
        text = msg['text'].strip().lower()
        logger.info(f"⌨️  User Command Detected: '{text}'")

        # Support both slash and non-slash versions
        if text in ['/status', 'status']:
            logger.info("🎰 Generating Bayesian Pivot Status Report...")
            self._send_status_report()
        elif text in ['/scan', 'scan']:
            logger.info("🔍 Generating Latest Bayesian Pivot Scan...")
            self._send_latest_scan_report()
        elif text in ['/guide', 'guide']:
            logger.info("📖 Sending Bayesian Pivot Command Guide...")
            self._send_command_guide()
        else:
            logger.debug(f"Ignored unknown message: {text}")

    def _send_status_report(self):
        """Sends a high-fidelity system status report via Telegram."""
        try:
            equity = self.tl.get_total_equity()
            
            # Daily Stats
            today = datetime.now().strftime('%Y-%m-%d')
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) FROM journal WHERE date(timestamp) = ?", (today,))
            trades_today, wins = c.fetchone()
            conn.close()
            
            btc_bias = self.scanner.get_detailed_bias("BTC/USD")
            alpha_val = getattr(self, 'alpha_mult', 1.0)
            
            def _t(txt): return f'<a href="https://t.me/bayesianpivot_bot">{txt}</a>'
            
            msg = (
                f"📊 <b>BAYESIAN PIVOT STATUS</b>\n\n"
                f"💰 <b>Equity:</b> {_t(f'${equity:,.2f}')}\n"
                f"📈 <b>Trades Today:</b> {_t(trades_today or 0)} (Wins: {_t(wins or 0)})\n"
                f"🧠 <b>Mood:</b> {_t(self.last_psych_state.get('sentiment', 'Neutral'))}\n"
                f"🛡️ <b>Risk Mult:</b> {_t(f'{self.risk_multiplier:.2f}x')} (Tilt: {_t(self.current_tilt_score)})\n"
                f"✨ <b>Alpha Persistence:</b> {_t(f'{alpha_val:.2f}x')}\n"
                f"🌎 <b>Market Pulse:</b> {_t(btc_bias)}\n\n"
                f"🕒 <i>Cycle #{self._cycle_count} Active</i>"
            )
            self.notifier._send_message(msg)
        except Exception as e:
            logger.error(f"Failed to generate status report: {e}")

    def _get_market_overview_ascii(self):
        """Generates a compact ASCII table of market states."""
        if not self.scan_results:
            return "No scan data available."
            
        lines = [
            "┌──────────┬─────────┬────────┬────────┐",
            "│ Symbol   │ Bias    │ Regime │ Hurst  │",
            "├──────────┼─────────┼────────┼────────┤"
        ]
        
        for symbol, data in self.scan_results.items():
            sym = symbol.split('/')[0]
            bias = str(data.get('bias', 'NEUTRAL'))[:7]
            regime = str(data.get('regime', 'CHOP'))[:6]
            hurst = f"{data.get('hurst', 0.5):.2f}"
            lines.append(f"│ {sym:<8} │ {bias:<7} │ {regime:<6} │ {hurst:<6} │")
            
        lines.append("└──────────┴─────────┴────────┴────────┘")
        return "\n".join(lines)

    def _send_latest_scan_report(self):
        """Sends the V3 Bayesian Pivot Briefing via Telegram on /scan, followed by reasoning and regime stats."""
        try:
            utc_now    = datetime.now(timezone.utc)
            active_kz  = _get_active_killzone(utc_now.hour)
            quartile_d = self.scanner.get_session_quartile()

            health_report = getattr(self, 'current_perf', {}) or {}
            dd_pct        = health_report.get('daily_drawdown', 0.0) * 100

            # ── Equity Buffer (Distance to Daily Stop in USD) ─────────────────
            live_equity = self.tl.get_total_equity()
            try:
                conn        = get_db_connection()
                dse_row     = conn.execute("SELECT value FROM sync_state WHERE key='daily_start_equity'").fetchone()
                conn.close()
                day_start   = float(dse_row['value']) if dse_row else live_equity
            except: day_start = live_equity
            daily_stop_level = day_start * (1 - self.prop_guardian.max_daily_drawdown)
            equity_buffer_usd = max(0.0, live_equity - daily_stop_level)
            health_report['equity_buffer_usd'] = equity_buffer_usd

            # ── Header data ───────────────────────────────────────────────────
            header_data = {
                'trust':           self.guard.get_trust_score(),
                'security':        self.guard.get_security_context(),
                'uptime':          str(timedelta(seconds=int(time.time() - self.session_start_time))),
                'cycle':           self._cycle_count,
                'kz_name':         active_kz['name'] if active_kz else 'OFF-HOURS',
                'sess_phase':      quartile_d.get('phase', 'Unknown'),
                'dd_pct':          dd_pct,
                'equity_buffer_usd': equity_buffer_usd,
            }

            # ── Account data ──────────────────────────────────────────────────
            open_positions = self.tl.get_open_positions()
            account_data   = {'equity': live_equity, 'positions': open_positions}

            # ── Trade performance (720h) ──────────────────────────────────────
            history        = self.tl.get_recent_history(hours=720)
            history_sorted = sorted(history, key=lambda x: x.get('close_time', ''), reverse=True)
            winning  = [t for t in history if t.get('pnl', 0) > 0]
            losing   = [t for t in history if t.get('pnl', 0) < 0]
            total_cl = len(history)
            win_rate = (len(winning) / total_cl * 100) if total_cl > 0 else 0
            avg_win  = (sum(t['pnl'] for t in winning) / len(winning)) if winning else 0
            avg_loss = (abs(sum(t['pnl'] for t in losing)) / len(losing)) if losing else 1
            avg_rr   = avg_win / avg_loss if avg_loss > 0 else 0
            performance_data = {
                'total_trades': total_cl,
                'win_rate':     win_rate,
                'avg_rr':       avg_rr,
                'avg_win':      avg_win,
                'avg_loss':     avg_loss,
                'recent':       history_sorted[:5],
            }

            # ── Confluence (intermarket) ───────────────────────────────────────
            mc  = getattr(self, '_last_market_context', {}) or {}
            confluence_data = {
                'dxy':             mc.get('DXY', {}),
                'nq':              mc.get('NQ',  {}),
                'tnx':             mc.get('TNX', {}),
                'alpha_mult':      getattr(self, 'alpha_mult', 1.0),
                'alpha_reasoning': getattr(self, 'alpha_reasoning', 'Initial baseline.'),
            }

            # ── Market rows (with live HTF draw per symbol) ───────────────────
            market_rows = []
            primary_symbol = "BTC/USD"
            for sym_full, data in self.scan_results.items():
                # Escape HTML special characters
                safe_sym = sym_full.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                draw_str = None
                try:
                    pois = self.scanner.detect_htf_pois(sym_full)
                    if pois:
                        df1m  = self.scanner.fetch_data(sym_full, '1m', limit=1)
                        price = float(df1m.iloc[-1]['close']) if df1m is not None else 0
                        nearest = min(pois, key=lambda p: abs(p['level'] - price)) if price else pois[0]
                        draw_str = f"{nearest['level']:,.4f} ({nearest['type']})"
                except: pass
                market_rows.append({
                    'symbol': safe_sym,
                    'bias':   data.get('bias', 'N/A'),
                    'regime': data.get('regime', 'CHOP'),
                    'hurst':  data.get('hurst', 0.5),
                    'draw':   draw_str,
                })

            # ── Latest DB setups (Dual: Accepted & Rejected) ──
            latest_accepted = None
            latest_rejected = None
            try:
                conn = get_db_connection()
                
                # 1. Fetch Latest Accepted Setup (verdict='ACCEPTED')
                row_acc = conn.execute(
                    "SELECT * FROM scans WHERE verdict='ACCEPTED' "
                    "ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                
                # 2. Fetch Latest Rejected Setup (verdict != 'ACCEPTED' and not a heartbeat)
                row_rej = conn.execute(
                    "SELECT * FROM scans "
                    "WHERE verdict != 'ACCEPTED' AND symbol != 'HEARTBEAT' AND verdict != 'SCAN_HEARTBEAT' "
                    "ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                
                conn.close()

                def _map_row(row):
                    if not row: return None
                    row = dict(row)
                    try:
                        ts = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                    except: ts = datetime.now(timezone.utc)
                    
                    safe_reasoning = (row.get('ai_reasoning') or "No reasoning available.").replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    safe_pattern   = (row.get('formations') or row.get('pattern', 'N/A')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    safe_db_sym    = row.get('symbol', 'N/A').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

                    return {
                        'symbol':   safe_db_sym,
                        'pattern':  safe_pattern,
                        'ai_score': row.get('ai_score', 'N/A'),
                        'regime':   row.get('shadow_regime', 'N/A'),
                        'reasoning': safe_reasoning,
                        'mins_ago': int((datetime.now(timezone.utc) - ts).total_seconds() / 60),
                        'smt':      row.get('smt_sponsorship', row.get('smt', 'N/A'))
                    }

                latest_accepted = _map_row(row_acc)
                latest_rejected = _map_row(row_rej)
            except Exception as db_err:
                logger.error(f"Failed to fetch dual setups: {db_err}")

            # Calculate Strategic Directive (Dynamic)
            directive = "Waiting for clean session sweep and structure shift."
            try:
                primary_symbol = "BTC/USD"
                df_1h = self.scanner.fetch_data(primary_symbol, "1h", limit=100, synchronized=False)
                df_4h = self.scanner._aggregate_ohlcv(self.scanner.fetch_data(primary_symbol, "1h", limit=400, synchronized=False), "4h")
                df_1m = self.scanner.fetch_data(primary_symbol, "1m", limit=5, synchronized=False)
                
                if df_1h is not None and df_4h is not None and df_1m is not None:
                    current_price = df_1m['close'].iloc[-1]
                    price_ranges = self.scanner.get_price_quartiles(primary_symbol) or {}
                    
                    def get_tf_bias(df):
                        if df is None or len(df) < 5: return 0
                        ema20 = df['close'].ewm(span=20).mean().iloc[-1]
                        ema50 = df['close'].ewm(span=50).mean().iloc[-1]
                        return 1 if ema20 > ema50 else -1

                    bias_4h = get_tf_bias(df_4h)
                    bias_1h = get_tf_bias(df_1h)
                    
                    if bias_4h == 1 and bias_1h == 1:
                        if current_price < price_ranges.get("Asian Range", {}).get("mid", 0):
                            directive = "🟢 <b>LOOK FOR:</b> Bullish MSS at Asian/London Low (Discount Buy opportunity)."
                        else:
                            directive = "⚠️ <b>LOOK FOR:</b> Expansion continuation above Asian High (breakout watch)."
                    elif bias_4h == -1 and bias_1h == -1:
                        directive = "🔴 <b>LOOK FOR:</b> Bearish rejection at Asian Range Premium/High (short opportunity)."
                    else:
                        directive = "⏳ <b>LOOK FOR:</b> Consolidation sweep. Session phase suggests waiting for Q2/Q3 shift."
            except Exception as e:
                logger.error(f"Failed to calculate strategic directive: {e}")

            # ── 1. SEND MAIN BRIEFING ──────────────────────────
            self.notifier.send_scan_briefing(
                header_data      = header_data,
                account_data     = account_data,
                performance_data = performance_data,
                confluence_data  = confluence_data,
                market_rows      = market_rows,
                latest_setup     = latest_accepted,
                latest_rejected  = latest_rejected,
                strategic_directive = directive
            )

            # ── 2. SEND STRATEGIC REASONING (Follow-up) ────────
            # Only send reasoning if the setup is "Fresh" (< 15 mins) or specifically requested
            main_setup = latest_accepted or latest_rejected
            is_fresh = main_setup and main_setup.get('mins_ago', 99) < 15
            
            if main_setup and is_fresh:
                reasoning_msg = (
                    f"🧠 <b>STRATEGIC REASONING (ACTIVE):</b>\n"
                    f"• Setup: <code>{main_setup['symbol']}</code> ({main_setup['pattern']})\n"
                    f"• AI Score: <code>{main_setup['ai_score']}/10</code>\n"
                    f"• SMT Sponsorship: <code>{main_setup['smt']}</code>\n\n"
                    f"<i>{main_setup['reasoning']}</i>"
                )
                self.notifier._send_message(reasoning_msg)
            elif main_setup:
                logger.info(f"Skipping reasoning follow-up: Stale setup detected ({main_setup.get('mins_ago')}m ago)")

            # ── 3. SEND MARKET REGIME STATS (Follow-up) ────────
            try:
                # Use BTC/USD for general regime stats
                df_hr = self.scanner.fetch_data(primary_symbol, Config.TIMEFRAME, limit=100)
                if df_hr is not None:
                    closes = df_hr['close'].values
                    hurst = self.scanner.get_hurst_exponent(closes)
                    adf_p = self.scanner.get_adf_test(closes)
                    
                    hurst_label = "Trending" if hurst > 0.55 else ("Mean-Reverting" if hurst < 0.45 else "Random Walk")
                    adf_label   = "Non-Stationary" if adf_p > 0.05 else "Stationary"
                    
                    regime_msg = (
                        f"🌎 <b>Market Regime Stats ({primary_symbol}):</b>\n"
                        f"• Hurst: <code>{hurst:.2f}</code> ({hurst_label})\n"
                        f"• ADF p-value: <code>{adf_p:.4f}</code> ({adf_label})"
                    )
                    self.notifier._send_message(regime_msg)
            except: pass

        except Exception as e:
            logger.error(f"High-Fidelity scan briefing failed: {e}", exc_info=True)



    def _send_command_guide(self):
        """Sends the pinned command guide to Telegram."""
        guide = (
            "🎯 <b>BAYESIAN PIVOT COMMAND GUIDE</b>\n\n"
            "Use these commands to interact with the system in real-time:\n\n"
            "📈 <b>/status</b> - Live equity, daily stats, and risk mood.\n"
            "🔍 <b>/scan</b> - Latest high-conviction setup detected.\n"
            "📖 <b>/guide</b> - View this command manual.\n\n"
            "🧠 <b>Interactive Features:</b>\n"
            "• <b>Sentiments:</b> Reply to hourly prompts with your mood to tune risk.\n"
            "• <b>Alpha Interviews:</b> When the bot asks 'Why?', provide your logic for SFT learning.\n\n"
            "<i>Pin this message for quick access.</i>"
        )
        self.notifier._send_message(guide)

    # PULSE PROTOCOL REMOVED (De-Modaled)

    def _get_recent_logs(self, n=15):
        """Reads the last n lines of the runner log and cleans them for Telegram."""
        try:
            log_path = "logs/local_runner.log"
            if not os.path.exists(log_path):
                return "No logs found."
            
            with open(log_path, "r") as f:
                lines = f.readlines()
            
            recent = lines[-n:]
            clean_lines = []
            for line in recent:
                # Remove common log metadata to save space: "2026-03-09 01:33:40,123 - Name - LEVEL - "
                parts = line.split(" - ")
                if len(parts) >= 3:
                    clean_lines.append(parts[-1].strip())
                else:
                    clean_lines.append(line.strip())
            
            return "\n".join(clean_lines)
        except Exception as e:
            return f"Error reading logs: {e}"

    def _get_dashboard_string(self):
        """Generates the same ASCII dashboard printed to terminal as a string."""
        now = datetime.now()
        cycle_time = now.strftime("%H:%M:%S")
        hour = now.hour
        session = "NY/LON" if 12 <= hour < 20 else ("LON" if 7 <= hour < 12 else "ASIA")
        
        q_data = self.scanner.get_session_quartile()
        kz_icon = "KZ: ON" if self.scanner.is_killzone() else "KZ: OFF"
        if self.scanner.is_asian_fade_window(): kz_icon = "⭐ FADE"
        
        perf = getattr(self, 'current_perf', {})
        dd_str = f"DD: {perf.get('daily_drawdown', 0):.1%} / {perf.get('total_drawdown', 0):.1%}"
        wr_rr = f"WR: {perf.get('win_rate', 0):.0%} | RR: {perf.get('avg_rr', 0):.1f}"
        mood = "FLOW" if self.risk_multiplier >= 1.0 else ("STRESS" if self.risk_multiplier > 0 else "HALT")
        
        active_positions = self.tl.get_open_positions()
        pos_count = len(active_positions)
        total_pnl = sum(p['pnl'] for p in active_positions)
        pnl_str = f"PnL: ${total_pnl:>+7.2f}"

        # Compact ASCII Box
        dash = [
            f"┌{'─'*38}┐",
            f"│ CYC #{self._cycle_count:<4} {cycle_time} {session:<6} {kz_icon:<8}│",
            f"│ {dd_str:<18} {wr_rr:<18} │",
            f"│ Mood: {mood:<10} {pnl_str:<18} │"
        ]
        
        if pos_count > 0:
            dash.append(f"├{'─'*38}┤")
            for p in active_positions[:2]: # Show max 2 to save space
                side = "L" if p['side'] == 'BUY' else "S"
                dash.append(f"│ {side} {p['symbol']:<10} @ {p['price']:<8.1f} PnL:${p['pnl']:>+6.1f} │")
        
        dash.append(f"└{'─'*38}┘")
        return "\n".join(dash)

    def _print_cycle_header(self):
        """Prints a high-fidelity dashboard of the current system state."""
        self._cycle_count += 1
        now = datetime.now()
        cycle_time = now.strftime("%Y-%m-%d %H:%M:%S")

        # Dead Zone Hourly Check
        now_utc = datetime.now(timezone.utc)
        if not hasattr(self, '_last_deadzone_hour'):
            self._last_deadzone_hour = -1
        if now_utc.hour != self._last_deadzone_hour:
            self._last_deadzone_hour = now_utc.hour
            if now_utc.hour in (7, 14, 16, 17, 22):
                try:
                    from src.clients.telegram_notifier import send_deadzone_alert
                    send_deadzone_alert("BTC/USD", "WATCHDOG", now_utc.hour)
                except Exception as ex:
                    logger.error(f"Failed to send local runner deadzone alert: {ex}")
        
        # Determine Session
        hour = now.hour
        session = "NY/LONDON" if 12 <= hour < 20 else ("LONDON" if 7 <= hour < 12 else "ASIA")
        
        # Get Quartile
        q_data = self.scanner.get_session_quartile()
        q_label = f"{q_data['phase']}"
        
        # KZ Icons
        kz_icon = "🔵 KILLZONE ACTIVE" if self.scanner.is_killzone() else "⚪️ OUTSIDE KILLZONE"
        if self.scanner.is_asian_fade_window(): kz_icon = "⭐ PRIME WINDOW (FADE)"
        
        # Last Signal
        last_sig_str = "None"
        if self._last_signal_time:
            delta = (datetime.now(timezone.utc) if self._last_signal_time.tzinfo else datetime.now()) - self._last_signal_time
            last_sig_str = f"{int(delta.total_seconds() / 60)}m ago"
            
        # Mood, Trust & Health
        mood = "🪷 Flow" if self.risk_multiplier >= 1.0 else (f"⚠️ Stress ({self.current_tilt_score})" if self.risk_multiplier > 0 else "🛑 HARD GUARD")
        trust = self.guard.get_trust_score()
        
        # Performance Metrics from PropGuardian (calculated in run_cycle)
        perf = getattr(self, 'current_perf', {})
        dd_str = f"DD: {perf.get('daily_drawdown', 0):.1%} / {perf.get('total_drawdown', 0):.1%}"
        wr_rr = f"WR: {perf.get('win_rate', 0):.0%} | RR: {perf.get('avg_rr', 0):.1f}"

        # --- Active Positions ---
        active_positions = self.tl.get_open_positions()
        pos_count = len(active_positions)
        total_pnl = sum(p['pnl'] for p in active_positions)
        pnl_icon = "💰" if total_pnl >= 0 else "📉"

        logger.info(f"└{'\u2500'*53}┘")

    def _print_market_overview(self):
        """Prints a compact table showing the scanned state of all symbols."""
        if not self.scan_results:
            return

        logger.info(f"┌{'\u2500'*61}┐")
        logger.info(f"│  MARKET STATE                                               │")
        logger.info( "├──────────┬─────────┬────────┬────────┬────────┬─────────────┤")
        logger.info(f"│ Symbol   │ Bias    │ Regime │ Hurst  │ SMT    │ Quartile    │")
        logger.info( "├──────────┼─────────┼────────┼────────┼────────┼─────────────┤")
        
        for symbol, data in self.scan_results.items():
            sym = symbol.split('/')[0]
            bias = str(data.get('bias', 'NEUTRAL'))[:7]
            regime = str(data.get('regime', 'CHOP'))[:6]
            hurst = f"{data.get('hurst', 0.5):.2f}"
            smt = f"{data.get('smt', 0.0):.2f}"
            quartile = str(data.get('quartile', 'N/A'))[:11]
            
            logger.info(f"│ {sym:<8} │ {bias:<7} │ {regime:<6} │ {hurst:<6} │ {smt:<6} │ {quartile:<11} │")
            
        logger.info( "└──────────┴─────────┴────────┴────────┴────────┴─────────────┘")

    def run_cycle(self):
        global log_scan
        logger.info("🚀 Starting Bayesian Pivot Scan Cycle...")
        self.scan_results = {} # Reset for this cycle
        self._handle_commands() # Listen for user requests
        self._print_cycle_header()
        
        try:
            # 1. Update Daily Start Equity (Anchor for drawdown)
            # Re-anchor if the stored value is from a previous day/session
            live_equity = self.tl.get_total_equity()
            if live_equity > 0:
                conn = get_db_connection()
                state = conn.execute("SELECT value, last_updated FROM sync_state WHERE key = 'daily_start_equity'").fetchone()
                
                needs_anchor = False
                if not state:
                    needs_anchor = True
                else:
                    last_updated = datetime.fromisoformat(state['last_updated'])
                    # If last updated more than 18 hours ago, or on a different calendar day
                    if (datetime.now() - last_updated).total_seconds() > 64800 or last_updated.day != datetime.now().day:
                        needs_anchor = True
                
                if needs_anchor:
                    logger.info(f"🔄 Re-anchoring Daily Start Equity to ${live_equity:,.2f}")
                    self.prop_guardian.update_daily_start(live_equity)
                conn.close()
            
            # 2. Account Health & Accountability Audit
            logger.info("🛡️ Prop Guardian: Auditing Account Health...")
            health_report = self.prop_guardian.check_account_health(live_equity)
            
            # ── V3 Equity Buffer calculation ─────────────────────────────────
            try:
                conn = get_db_connection()
                dse_row = conn.execute("SELECT value FROM sync_state WHERE key = 'daily_start_equity'").fetchone()
                conn.close()
                day_start = float(dse_row['value']) if dse_row else live_equity
            except: day_start = live_equity
                
            daily_stop_level = day_start * (1 - self.prop_guardian.max_daily_drawdown)
            equity_buffer_usd = max(0.0, live_equity - daily_stop_level)
            health_report['equity_buffer_usd'] = equity_buffer_usd
            self.current_perf = health_report
            
            # Get current session for psychology check
            utc_now = datetime.now(timezone.utc)
            current_kz = _get_active_killzone(utc_now.hour)
            current_session_name = current_kz['name'] if current_kz else 'OFF-HOURS'

            # 3. Biometric & Psychology Audit (Non-blocking Session-gated)
            # Only trigger on new premium session start (exclude off-hours to avoid spamming)
            if current_session_name != 'OFF-HOURS' and current_session_name != self.last_session_name and not self.awaiting_psych_response:
                logger.info(f"🧠 Prompting User for Psychology Update for {current_session_name}...")
                self.notifier._send_message(f"🧠 *BAYESIAN PIVOT SENTIMENT:* {current_session_name} started. How are you feeling right now? (Reply within 3 mins or we default to safe risk)")
                self.awaiting_psych_response = True
                self.last_psych_prompt_time = int(time.time())
                self.last_session_name = current_session_name
            elif current_session_name == 'OFF-HOURS':
                self.last_session_name = 'OFF-HOURS'
                
            if self.awaiting_psych_response:
                user_response = self.notifier.get_latest_message(since_timestamp=self.last_psych_prompt_time)
                if user_response:
                    physio_tilt = 1.0 if getattr(Config, 'BYPASS_BIOMETRIC_GATE', False) else self.biometrics.calculate_physio_tilt()
                    psych_state = self.psychology.analyze_user_state(user_response['text'], physio_tilt=physio_tilt)
                    self.last_psych_state = psych_state
                    self.awaiting_psych_response = False
                    mood = psych_state.get('sentiment', 'Unknown')
                    score = psych_state.get('tilt_score', 0)
                    mult = self.psychology.get_risk_multiplier(score)
                    self.notifier._send_message(f"✅ *SENTIMENT CAPTURED*\n• Mood: `{mood}`\n• Tilt Score: `{score}/10`\n• Risk Multiplier: `{mult}x`")
                elif time.time() - self.last_psych_prompt_time > 180: # 3-minute timeout
                    logger.info("🕒 Psychology prompt timeout. Defaulting to baseline risk.")
                    self.awaiting_psych_response = False

            # Calculate risk multiplier (default to 0.5x while awaiting response)
            if self.awaiting_psych_response:
                psych_mult = 0.5
            else:
                self.current_tilt_score = self.last_psych_state.get('tilt_score', 1)
                psych_mult = self.psychology.get_risk_multiplier(self.current_tilt_score)
                
            self.risk_multiplier = min(psych_mult, health_report['risk_multiplier'])
            
            if health_report['status'] != "HEALTHY":
                logger.warning(f"🛡️ PROP GUARD: {health_report['message']}")
            
            if self.risk_multiplier == 0:
                logger.error("🛑 HARD SHUTDOWN: Risk Shield Active. Skipping cycle.")
                return

            # 1. Sync Equity
            live_equity = self.tl.get_total_equity()
            if live_equity > 0:
                 update_sync_state(live_equity, 0)
            
            # 2. Journal Watchdog
            self._sync_trade_journal(live_equity)
            
            scan_list = Config.SYMBOLS + Config.ALT_SYMBOLS
            setups_found = 0

            # 3. INTERMARKET CONTEXT BATCHING
            market_context = self.scanner.intermarket.get_market_context()
            self._last_market_context = market_context
            dxy_trend = market_context.get('DXY', {}).get('trend', 'N/A') if market_context else 'N/A'

            # 4. ICT SESSION CLASSIFICATION
            utc_now      = datetime.now(timezone.utc)
            utc_h, utc_m = utc_now.hour, utc_now.minute
            active_kz    = _get_active_killzone(utc_h)
            presession_kz = _is_presession_window(utc_h, utc_m)

            quartile_data = self.scanner.get_session_quartile()
            session_info  = {
                'name':  active_kz['name'] if active_kz else 'OFF-HOURS',
                'phase': quartile_data.get('phase', 'Unknown'),
            }

            # 5. PRE-SESSION GUARD SCAN (15 min before each killzone open, once per session)
            if presession_kz:
                scan_key = f"{presession_kz['name']}_{utc_now.date()}"
                if scan_key not in self._presession_scanned:
                    logger.info(f"🛡️ PRE-SESSION GUARD SCAN: {presession_kz['name']} opens in ~15 min")
                    # Run all lightweight scans synchronously (already in guard thread, just fetch result)
                    guard_ctx = self.guard.get_security_context()
                    self.notifier._send_message(
                        f"🛡️ <b>PRE-SESSION SWEEP: {presession_kz['name']}</b>\n"
                        f"Environment check 15 min before killzone open.\n"
                        f"• Result: <code>{guard_ctx}</code>"
                    )
                    self._presession_scanned.add(scan_key)
                    # Prune old keys to avoid unbounded growth
                    if len(self._presession_scanned) > 20:
                        self._presession_scanned.pop()

            for symbol in scan_list:
                cached_ctx = {'intermarket': market_context} if market_context else None

                # ── Bias Synthesis (Daily + 4H + DXY) ────────────────────────
                daily_bias = self.scanner.get_detailed_bias(symbol, index_context=market_context)
                htf_bias   = self.scanner.get_4h_bias(symbol)
                bias_score = daily_bias.split('(')[0].strip() if '(' in daily_bias else daily_bias
                bias_data  = {
                    'daily':     daily_bias,
                    'htf':       htf_bias,
                    'dxy_trend': dxy_trend,
                    'smt_score': f"{market_context.get('DXY', {}).get('change_ltf', 0):+.2f}%" if market_context else 'N/A',
                    'bias_conflict': "NEUTRAL" in daily_bias or "CONFLICT" in daily_bias,
                }

                # ── Hurst Gate & Outlier Inducement Tracking (Strategy 9) ───────
                hurst_val = 0.5
                try:
                    df_tmp = self.scanner.fetch_data(symbol, Config.TIMEFRAME, limit=100)
                    if df_tmp is not None and len(df_tmp) >= 30:
                        # 1. Strategy 9: Track Extreme Outliers & Judas Inducements
                        outlier_event = self.inducement_tracker.detect_outlier_candle(df_tmp, idx=-1)
                        if outlier_event:
                            self.inducement_tracker.log_inducement_event(outlier_event)

                        # 2. Calculate Hurst Exponent
                        hurst_val = self.scanner.get_hurst_exponent(df_tmp['close'].values)
                except Exception as e:
                    logger.debug(f"Outlier/Hurst tracking error on {symbol}: {e}")

                # 🚫 THE MEAT GRINDER: Block symbols in the 0.45 - 0.55 randomness zone
                if Config.HURST_CHAOS_RANGE[0] <= hurst_val <= Config.HURST_CHAOS_RANGE[1]:
                    logger.debug(f"🚫 {symbol} in MEAT GRINDER (Hurst: {hurst_val:.2f}). Skipping.")
                    continue

                if hurst_val < Config.HURST_MAX_RANDOM:
                    hunt_label = "Turtle Soup / Fade Search"
                    strategy_mode = "REVERSAL"
                elif hurst_val > Config.HURST_MIN_MEMORY:
                    hunt_label = "Trend Alignment / Displacement Search"
                    strategy_mode = "TREND"
                else:
                    # Fallback — should be caught by chaos range but for safety:
                    continue

                # ── HTF POI — Liquidity Gravity ───────────────────────────────
                # Publish Pre-Computed Macro AI Bias to AIPermissionMap for Zero-Latency Quant Gating
                try:
                    from src.engines.ai_permission_map import AIPermissionMap
                    clean_bias = "BULLISH" if "BULLISH" in daily_bias.upper() else ("BEARISH" if "BEARISH" in daily_bias.upper() else "NEUTRAL")
                    AIPermissionMap.set_permission(
                        symbol=symbol,
                        ai_bias=clean_bias,
                        conviction_score=8.5 if not bias_data['bias_conflict'] else 6.0,
                        regime=strategy_mode,
                        rag_similarity=85.0 if not bias_data['bias_conflict'] else 50.0,
                        authorized_archetypes=[
                            "JUDAS_INDUCEMENT_SNIPER",
                            "LONDON_CLOSE_SILVER_BULLET",
                            "TURTLE_SOUP_LIQUIDITY_SWEEP",
                            "STRAT_5_XAU_GOLD_50PCT_CE_LONG",
                            "FVG_50PCT_CE_REVERSAL_LONG"
                        ],
                        notes=f"Daily: {daily_bias} | 4H: {htf_bias} | Hurst: {hurst_val:.2f}"
                    )
                except Exception as perm_err:
                    logger.debug(f"AIPermissionMap publish error: {perm_err}")
                liquidity_targets = None
                try:
                    pois = self.scanner.detect_htf_pois(symbol)
                    if pois:
                        # Prefer the closest unmitigated POI to current price
                        try:
                            df_1m  = self.scanner.fetch_data(symbol, '1m', limit=1)
                            price  = float(df_1m.iloc[-1]['close']) if df_1m is not None else 0
                        except: price = 0
                        nearest = min(pois, key=lambda p: abs(p['level'] - price)) if price else pois[0]
                        pip_size = 0.0001 if 'JPY' not in symbol else 0.01
                        dist_pips = abs(nearest['level'] - price) / pip_size if price else 0
                        liquidity_targets = {
                            'target_price': nearest['level'],
                            'target_type':  nearest['type'],
                            'distance_pips': dist_pips,
                        }
                except Exception as _poi_err:
                    logger.debug(f"HTF POI error for {symbol}: {_poi_err}")

                self.scan_results[symbol] = {
                    'bias':     bias_score,
                    'regime':   'TREND' if strategy_mode == "TREND" else 'REVERSAL',
                    'hurst':    hurst_val,
                    'smt':      market_context.get('DXY', {}).get('change_ltf', 0) if market_context else 0.0,
                    'quartile': f"Q{quartile_data['num']}: {quartile_data['phase'][:7]}",
                }

                # Gates
                cal_safe, cal_reason = self.cal_filter.is_safe_to_trade(symbol)
                if not cal_safe: continue

                # ── Strategy-Aligned Scanning ────────────────────────────────
                is_prime_window = self.scanner.is_asian_fade_window()
                result = None
                
                # 1. Strategy 9: Judas Outlier Inducement Hunter (AI-Gated Institutional Verification)
                if getattr(Config, 'STRATEGY_9_ENABLED', True):
                    strat9_setup = self.judas_engine.evaluate_dataframe(df_tmp, symbol=symbol)
                    if strat9_setup:
                        logger.info(f"🎯 [STRATEGY 9 CANDIDATE] Evaluating Judas Inducement on {symbol} ({strat9_setup['direction']})")
                        _entry = strat9_setup['entry_price']
                        _sl = strat9_setup['stop_loss']
                        _tp = strat9_setup['take_profit']
                        lots = strat9_setup['calculated_lots']
                        direction = strat9_setup['direction']
                        exec_side = "buy" if direction == "LONG" else "sell"

                        # Compute Real Hurst Exponent for Regime Verification
                        hurst_val = self.calculate_hurst(df_tmp['close']) if hasattr(self, 'calculate_hurst') else 0.60
                        
                        # Real Gemini AI Validation
                        live_score = 0.0
                        try:
                            ai_result = validate_setup(
                                strat9_setup,
                                self.sentiment_engine.get_market_sentiment(symbol),
                                self.sentiment_engine.get_whale_confluence(),
                                df=df_tmp,
                                exchange=self.scanner.exchange if hasattr(self.scanner, 'exchange') else None,
                                hurst_exponent=hurst_val,
                                guard_trust_score=self.guard.get_trust_score()
                            )
                            live_eval = ai_result.get('live_execution', ai_result)
                            live_score = float(live_eval.get('score', 0.0))
                            logger.info(f"🤖 [STRATEGY 9 AI AUDIT] {symbol} AI Score: {live_score:.1f}/10 (Hurst: {hurst_val:.3f})")
                        except Exception as ai_err:
                            logger.warning(f"Strategy 9 AI validation error for {symbol}: {ai_err}")
                            live_score = 0.0

                        strat9_setup['ai_score'] = live_score
                        strat9_setup['confidence_score'] = live_score

                        now_utc = datetime.now(timezone.utc)
                        is_weekend = now_utc.weekday() in [5, 6]  # Saturday (5), Sunday (6)
                        
                        # Institutional Gate: Score must be >= 8.5/10.0 and Hurst >= 0.55
                        min_score = getattr(Config, 'AI_CONVICTION_THRESHOLD', 8.5)
                        if live_score < min_score or hurst_val < 0.55 or is_weekend:
                            logger.info(f"🛡️ [STRATEGY 9 FILTERED] Score {live_score:.1f} < {min_score} or Hurst {hurst_val:.3f} < 0.55 -> Routed to $0 Shadow Simulation.")
                            if hasattr(self, 'counterfactual_tracker'):
                                self.counterfactual_tracker.log_shadow_trade(strat9_setup, reason="Sub-Threshold AI Score / Range Chop")
                        elif getattr(Config, 'STRATEGY_9_AUTO_EXECUTE', False) and getattr(Config, 'LIVE_AUTO_EXECUTION', False):
                            # Send dedicated Telegram Alert for verified execution
                            self.notifier._send_message(self.judas_engine.format_telegram_alert(strat9_setup))
                            try:
                                exec_res = self.tl.execute_trade_across_all_accounts(
                                    symbol=symbol,
                                    side=exec_side,
                                    entry_price=_entry,
                                    stop_loss=_sl,
                                    take_profit=_tp,
                                    risk_scale=1.00,
                                    tranche_label="STRATEGY_9_JUDAS",
                                    ai_score=live_score,
                                    has_smt=True,
                                    session="LONDON_KILLZONE" if now_utc.hour in range(7, 10) else "NY_KILLZONE",
                                    hurst_exponent=hurst_val
                                )
                                trade_success = exec_res.get("success", False)
                                if trade_success:
                                    logger.info(f"✅ Strategy 9 Trade placed across {exec_res.get('filled_count', 0)} accounts on TradeLocker: {exec_side.upper()} on {symbol} (SL: ${_sl:,.2f}, TP: ${_tp:,.2f})")
                                    self.notifier._send_message(f"⚡ <b>STRATEGY 9 AUTO-EXECUTION SUCCESS:</b> Placed <a href='https://t.me/bayesianpivot_bot'>{exec_side.upper()}</a> on {symbol} across {exec_res.get('filled_count', 0)} accounts (SL: <b>${_sl:,.2f}</b>, TP: <b>${_tp:,.2f}</b>)")
                                else:
                                    logger.error(f"❌ Strategy 9 Trade rejected by TradeLocker broker client or 10-Gate Firewall.")
                            except Exception as exec_err:
                                logger.error(f"❌ Strategy 9 execution error: {exec_err}", exc_info=True)

                        # Sign and log to ledger
                        if self.ledger:
                            self.ledger.sign_signal(strat9_setup, live_score)

                        # Proceed to next symbol to preserve execution priority
                        continue


                # 2. Reversal scans in Reversal markets (with fallback)
                if not result and strategy_mode == "REVERSAL":
                    if is_prime_window:
                        result = self.scanner.scan_asian_fade(symbol)
                    if not result:
                        result = self.scanner.scan_order_flow(symbol, timeframe=Config.TIMEFRAME, cached_context=cached_ctx)
                    if not result:
                        result = self.scanner.scan_pattern(symbol, timeframe=Config.TIMEFRAME, cached_context=cached_ctx)
                
                # 3. Trend scans in Trending markets (with fallback)
                elif not result and strategy_mode == "TREND":
                    result = self.scanner.scan_trend_expansion(symbol, timeframe=Config.TIMEFRAME, cached_context=cached_ctx)
                    if not result:
                        result = self.scanner.scan_pattern(symbol, timeframe=Config.TIMEFRAME, cached_context=cached_ctx)

                if result:
                    setup, df = result
                    if not setup: continue

                    # ── 98% Reliability: Slippage Hard Floor ────────────────
                    try:
                        ticker = self.scanner.exchange.fetch_ticker(symbol)
                        current_spread = ticker['ask'] - ticker['bid']
                        atr_vals = self.scanner.calculate_atr(df)
                        current_atr = float(atr_vals.iloc[-1]) if not atr_vals.empty else 0
                        
                        if current_atr > 0:
                            spread_to_atr = current_spread / current_atr
                            if spread_to_atr > Config.get('SLIPPAGE_ATR_RATIO_MAX', 1.5):
                                logger.warning(f"🚫 Slippage Floor Breach for {symbol}: Spread/ATR={spread_to_atr:.2f} (Limit: {Config.get('SLIPPAGE_ATR_RATIO_MAX', 1.5)})")
                                continue
                    except Exception as e:
                        logger.error(f"Slippage check failed for {symbol}: {e}")
                        continue

                    # Correlation & Regime
                    direction = setup.get('direction', setup.get('bias', ''))
                    corr_ok, _ = self.corr_gate.check(symbol, direction)
                    if not corr_ok: continue

                    df_1h = self.scanner.fetch_data(symbol, '1h', limit=200)
                    regime_result = self.regime_filter.classify(symbol, df_1h, df)
                    if not regime_result.allowed: continue

                    self.scan_results[symbol]['regime'] = regime_result.regime.value
                    setup['regime'] = regime_result.regime.value
                    setup['regime_type'] = regime_result.regime.value
                    setup['hurst'] = regime_result.hurst
                    setup['atr_percentile'] = regime_result.atr_percentile

                    # ── AI Validation (Cloud → Llama3 fallback) ───────────────
                    session_info_for_llm = session_info
                    if getattr(Config, 'BYPASS_AI_GATE', False):
                        logger.info(f"⚡ AI Gate Bypassed (Bayesian Pivot Light) for {symbol}")
                        ai_result = {
                            "live_execution": {"score": 10.0, "reasoning": "AI Gate Bypassed (Bayesian Pivot Light)"},
                            "shadow_optimizer": {"suggested_risk_multiplier": 1.0}
                        }
                        live = ai_result["live_execution"]
                        live_score = 10.0
                    else:
                        try:
                            ai_result = validate_setup(
                                setup, self.sentiment_engine.get_market_sentiment(symbol),
                                self.sentiment_engine.get_whale_confluence(),
                                df=df, exchange=self.scanner.exchange, hurst_exponent=hurst_val,
                                guard_trust_score=self.guard.get_trust_score()
                            )
                            live = ai_result.get('live_execution', ai_result)
                            live_score = live.get('score', 0)
                        except Exception as _cloud_err:
                            logger.warning(f"☁️ Cloud AI failed for {symbol}: {_cloud_err} — trying MLX LoRA...")
                            if self.local_llm.is_available():
                                try:
                                    local_result = self.local_llm.score_setup(
                                        setup, market_context=market_context,
                                        hurst=hurst_val, session_info=session_info_for_llm
                                    )
                                    live = local_result
                                    live_score = local_result.get('score', 0)
                                    logger.info(f"🤖 MLX LoRA fallback score for {symbol}: {live_score}")
                                except Exception as _llm_err:
                                    logger.error(f"🤖 MLX LoRA also failed: {_llm_err}")
                                    continue
                            else:
                                continue

                    # Hurst-aware pattern label
                    base_pattern = setup.get('pattern', 'Unknown')
                    enriched_pattern = f"{hunt_label} — {base_pattern}"

                    # ── Multi-Account Funnel Matrix & Counterfactual Agent Dispatch ──
                    smt_val = setup.get('smt_strength', 0.0)
                    if smt_val == 0.0:
                        if setup.get('is_judas_inducement', False) or setup.get('is_asian_fade', False):
                            smt_val = 0.50  # Boundary / Judas sweeps inherit implicit liquidity sponsorship
                        else:
                            dxy_chg = abs(market_context.get('DXY', {}).get('change_ltf', 0)) if market_context else 0.0
                            smt_val = max(smt_val, dxy_chg * 10.0 if dxy_chg > 0 else 0.0)

                    all_open_positions = self.tl_client.get_open_positions() if hasattr(self, 'tl_client') and self.tl_client else []
                    
                    for acc_key in self.funnel_manager.profiles.keys():

                        passed, rejection_reasons = self.funnel_manager.evaluate_setup_for_account(
                            setup=setup,
                            account_key=acc_key,
                            hurst=hurst_val,
                            smt_strength=smt_val,
                            slippage_ratio=spread_to_atr if 'spread_to_atr' in locals() else 0.0,
                            cal_safe=cal_safe,
                            corr_ok=corr_ok,
                            regime_allowed=regime_result.allowed,
                            ai_score=live_score,
                            open_positions=all_open_positions,
                            regime_type=regime_result.regime.value
                        )


                        if passed:
                            logger.info(f"🎯 Setup PASSED Funnel Validator for {acc_key} ({symbol} {direction})")
                        else:
                            profile = self.funnel_manager.profiles[acc_key]
                            self.counterfactual_tracker.register_shadow_trade(
                                setup=setup,
                                account_key=acc_key,
                                strategy_mode=profile.strategy_mode,
                                rejection_reasons=rejection_reasons
                            )

                    # Threshold: Bayesian Pivot Standard (8.5)
                    is_asian_fade = setup.get('is_asian_fade', False)
                    if is_asian_fade:
                        threshold = Config.AI_THRESHOLD_ASIAN_FADE
                    else:
                        threshold = Config.AI_THRESHOLD_LONG if direction == 'LONG' else Config.AI_THRESHOLD_SHORT

                    # ── 100% DISCIPLINED ARCHETYPE QUARANTINE ──
                    # ONLY Turtle Soup Liquidity Sweeps / Judas Sweeps are LIVE.
                    # Trend Pullbacks, Order Blocks, and Standard FVGs are 100% Shadow Lab ($0.00 Live Risk / $0 Telegram noise).
                    is_sweep_strategy = any(k in base_pattern.upper() for k in ["TURTLE", "SWEEP", "JUDAS", "ASIAN FADE"])
                    if not is_sweep_strategy:
                        logger.info(f"👻 Non-sweep pattern quarantined to Shadow Lab (No Live Alert): {enriched_pattern}")
                        continue

                    if live_score >= threshold:

                        setups_found += 1
                        self._last_signal_time = datetime.now(timezone.utc)

                        # Sizing
                        calc_equity = live_equity if live_equity > 0 else 100000.0
                        
                        # 98% Reliability: Staged Risk Reduction (Consolidated into AIValidator)
                        shadow = ai_result.get('shadow_optimizer', {})
                        ai_multiplier = shadow.get('suggested_risk_multiplier', 1.0)
                        
                        # Calculate true R:R from actual prices (not hardcoded config)
                        _entry = setup['entry']
                        _sl    = setup['stop_loss']
                        _tp    = setup.get('target')
                        _risk  = abs(_entry - _sl)
                        _actual_rr = round(abs(_tp - _entry) / _risk, 2) if (_tp and _risk > 0) else 0

                        # TP Sanity Check
                        _tp_valid = False
                        if _tp is not None:
                            if direction == 'LONG' and _tp > _entry:
                                _tp_valid = True
                            elif direction == 'SHORT' and _tp < _entry:
                                _tp_valid = True

                        if not _tp_valid:
                            logger.error(f"🚨 ALERT BLOCKED: {symbol} {direction} TP Sanity Fail | Entry={_entry:.2f} | TP={_tp} (Negative Return)")
                            continue

                        # Risk Calculation (Target Profit Mode vs Fixed USD Risk vs standard Risk Per Trade)
                        variant_mult = ai_multiplier * regime_result.suggested_size_mult * psych_mult * getattr(self, 'alpha_mult', 1.0)
                        if getattr(Config, 'VARIANT_SIZING_SHADOW_MODE', True):
                            risk_amt = getattr(Config, 'FIXED_RISK_USD', 100.0)
                            logger.info(f"🛡️ [VARIANT SIZING SHADOW MODE] Flat risk ${risk_amt:.2f} active for live execution. Variant Mult ({variant_mult:.2f}x) tracked in shadow logs.")
                        elif getattr(Config, 'TARGET_PROFIT_MODE', False) and _actual_rr > 0:
                            risk_amt = Config.TARGET_PROFIT_USD / _actual_rr
                            if direction == 'LONG':
                                risk_amt = risk_amt * getattr(Config, 'LONG_RISK_MULTIPLIER', 1.0)
                            logger.info(f"🎯 TARGET PROFIT MODE: Risking ${risk_amt:.2f} to target ${Config.TARGET_PROFIT_USD:.2f} profit with R:R {_actual_rr:.2f}")
                        elif getattr(Config, 'FIXED_RISK_USD', None) is not None:
                            risk_amt = Config.FIXED_RISK_USD
                            if direction == 'LONG':
                                risk_amt = risk_amt * getattr(Config, 'LONG_RISK_MULTIPLIER', 1.0)
                            logger.info(f"🛡️ FIXED RISK MODE: Risking a hard limit of ${risk_amt:.2f} per trade")
                        else:
                            base_risk_pct = Config.RISK_PER_TRADE
                            risk_mult = variant_mult
                            if direction == 'LONG':
                                risk_mult = risk_mult * getattr(Config, 'LONG_RISK_MULTIPLIER', 1.0)
                            if setup.get('bias_conflict'):
                                risk_mult = risk_mult * 0.5
                                logger.warning(f"⚠️ BIAS CONFLICT: Reducing risk to 50% for {symbol}")
                            risk_amt = calc_equity * base_risk_pct * risk_mult
                        # Strict Risk Cap (Bayesian Pivot Guard Cap)
                        max_risk = getattr(Config, 'MAX_RISK_USD', 150.0)
                        if risk_amt > max_risk:
                            logger.warning(f"🛡️ Risk calculated as ${risk_amt:.2f} exceeds MAX_RISK_USD (${max_risk:.2f}). Capping to ${max_risk:.2f}.")
                            risk_amt = max_risk
                        
                        lots = round(risk_amt / _risk, 4) if _risk > 0 else 0
                        
                        # 1. Asset-Specific Symbol Caps (from config.py) 
                        max_allowed_size = getattr(Config, 'MAX_POSITION_SIZES', {}).get(symbol)
                        if max_allowed_size is not None and lots > max_allowed_size:
                            logger.warning(f"⚠️ {symbol} lot size ({lots}) exceeds symbol cap. Capping to {max_allowed_size}.")
                            lots = max_allowed_size

                        # 2. Notional USD Value Cap (Bayesian Pivot Safety) <!-- id: 12 -->
                        position_value = lots * setup['entry']
                        max_notional = getattr(Config, 'MAX_NOTIONAL_VALUE_USD', 40000.0)
                        
                        if position_value > max_notional:
                            lots = round(max_notional / setup['entry'], 2)
                            position_value = lots * setup['entry']
                            logger.warning(f"🛡️ {symbol} exceeds Notional Cap (${max_notional:,}). Capping to {lots} lots (${position_value:,.2f}).")

                        # 3. Take Profit Cap (Strict $400 Profit Cap)
                        _max_profit = getattr(Config, 'MAX_PROFIT_USD', 400.0)
                        if _tp is not None and lots > 0:
                            _potential_profit = lots * abs(_tp - _entry)
                            if _potential_profit > _max_profit:
                                logger.warning(f"🛡️ Potential profit ${_potential_profit:.2f} exceeds MAX_PROFIT_USD (${_max_profit:.2f}). Clamping Take Profit.")
                                if direction == 'LONG':
                                    _tp = _entry + (_max_profit / lots)
                                else:
                                    _tp = _entry - (_max_profit / lots)
                                setup['target'] = _tp

                        risk_calc = {
                            "entry": _entry, 
                            "stop_loss": _sl,
                            "take_profit": _tp,
                            "position_size": lots, 
                            "position_value": position_value,
                            "variant_mult": variant_mult,
                            "regime_mult": regime_result.suggested_size_mult,
                            "psych_mult": psych_mult, 
                            "alpha_mult": getattr(self, 'alpha_mult', 1.0)
                        }

                        signal_id = self.ledger.sign_signal(setup, live_score) if self.ledger else "UNSIGNED"
                        self.corr_gate.register(signal_id, symbol, direction)

                        self.notifier.send_alert(
                            symbol=symbol,
                            timeframe=Config.TIMEFRAME,
                            pattern=enriched_pattern,
                            ai_score=live_score,
                            reasoning=live.get('reasoning', ''),
                            risk_calc=risk_calc,
                            regime_result=regime_result,
                            health_report=health_report,
                            bias_data=bias_data,
                            liquidity_targets=liquidity_targets,
                            session_info=session_info,
                            security_status=self.guard.get_security_context(),
                            psych_data={'mood': self.last_psych_state.get('sentiment', 'Unknown')},
                            direction=direction
                        )

                        # ── LIVE AUTO-EXECUTION (Strict Weekday Killzone Gated) ──
                        now_utc = datetime.now(timezone.utc)
                        is_weekend = now_utc.weekday() in [5, 6]
                        if is_weekend:
                            logger.info(f"🚫 [WEEKEND CHOP LOCK] {symbol} setup quarantined to $0 Shadow tracking (Zero Live Risk on Weekends).")
                        elif Config.LIVE_AUTO_EXECUTION and live_score >= threshold:
                            logger.info(f"⚡ LIVE AUTO-EXECUTION ({live_score}/10 Setup): Submitting order to TradeLocker: {direction} {lots} {symbol} SL={_sl} TP={_tp}")
                            try:
                                dir_str = str(direction or "BUY").upper()
                                exec_side = "buy" if dir_str == "LONG" or dir_str == "BUY" else "sell"
                                exec_res = self.tl.execute_trade_across_all_accounts(
                                    symbol=symbol,
                                    side=exec_side,
                                    entry_price=_entry,
                                    stop_loss=_sl,
                                    take_profit=_tp,
                                    risk_scale=1.00,
                                    tranche_label="FULL_SIZE_ENTRY",
                                    ai_score=live_score,
                                    has_smt=bool(getattr(regime_result, 'smt_active', False) or (isinstance(session_info, dict) and session_info.get('smt_divergence', False))),
                                    session=session_info.get('session', '') if isinstance(session_info, dict) else str(session_info),
                                    hurst_exponent=float(getattr(regime_result, 'hurst', 0.58))
                                )
                                trade_success = exec_res.get("success", False)
                                if trade_success:
                                    logger.info(f"✅ Trade executed successfully across {exec_res.get('filled_count', 0)} accounts on TradeLocker.")
                                    self.notifier._send_message(f"⚡ <b>AUTO-EXECUTION SUCCESS:</b> Placed <a href='https://t.me/bayesianpivot_bot'>{exec_side.upper()}</a> on {symbol} across {exec_res.get('filled_count', 0)} accounts (SL: <b>${_sl:,.2f}</b>, TP: <b>${_tp:,.2f}</b>)")
                                else:
                                    logger.error(f"❌ Trade execution rejected by TradeLocker broker client.")
                                    self.notifier._send_message(f"⚠️ <b>AUTO-EXECUTION FAILURE:</b> Broker rejected order request for {symbol}.")
                            except Exception as exec_err:
                                logger.error(f"❌ Error executing live TradeLocker trade: {exec_err}", exc_info=True)
                                self.notifier._send_message(f"🚨 <b>AUTO-EXECUTION EXCEPTION:</b> {str(exec_err)}")
                        elif Config.LIVE_AUTO_EXECUTION:
                            logger.info(f"ℹ️ Skipping auto-execution: setup score is {live_score}/10 (Required threshold: {threshold}/10).")

                        # ── V3 Persistence ──
                        try:
                            scan_data = {
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                                'symbol': symbol,
                                'direction': direction,
                                'pattern': enriched_pattern,
                                'formations': enriched_pattern,
                                'bias': bias_score,
                                'verdict': 'ACCEPTED',
                                'shadow_regime': regime_result.regime.value,
                                'shadow_multiplier': regime_result.suggested_size_mult,
                                'session': session_info['name'],
                                'killzone': 'ON' if self.scanner.is_killzone() else 'OFF',
                                'hurst': hurst_val,
                                'smt_strength': market_context.get('DXY', {}).get('change_ltf', 0) if market_context else 0.0,
                                'daily_pnl': self.current_perf.get('daily_pnl', 0),
                                'total_pnl': self.current_perf.get('total_pnl', 0),
                                'bias_conflict': bias_data.get('bias_conflict', False)
                            }
                            log_scan(scan_data, live)
                        except Exception as p_err:
                            logger.error(f"Failed to persist accepted scan: {p_err}")
            
            if self._cycle_count % 15 == 0:
                self.audit_engine.run_audit(hours_back=12)
                if self.ledger: self._audit_rogue_ledger()

            if self.retrain_loop: self.retrain_loop.run_if_due()
            # ── Counterfactual Agent Shadow Trade Evaluation ───────────────
            # Poll incoming Telegram commands (/report, /kill, inline buttons)
            if hasattr(self, 'tg') and self.tg:
                self.tg_offset = getattr(self, 'tg_offset', None)
                self.tg_offset = self.tg.poll_updates_and_dispatch(self.tg_offset)

            try:
                resolved = self.counterfactual_tracker.evaluate_open_shadow_trades(self.scanner)
                if resolved > 0:
                    logger.info(f"👻 Counterfactual Agents resolved {resolved} shadow trades this cycle.")
            except Exception as _cf_err:
                logger.error(f"Counterfactual evaluation error: {_cf_err}")


            self._print_market_overview()


            if setups_found == 0:
                btc_bias = self.scanner.get_detailed_bias("BTC/USD", index_context=market_context)
                if time.time() - self._last_heartbeat_time > 900:
                    log_scan({'timestamp': datetime.now(timezone.utc).isoformat(), 'symbol': 'HEARTBEAT', 'pattern': 'System Active', 'bias': btc_bias, 'direction': 'NEUTRAL', 'verdict': 'SCAN_HEARTBEAT'}, {'score': 0, 'reasoning': 'Active Polling'})
                    self._last_heartbeat_time = time.time()
                # Opinionated Bias: 15-minute Market Pulse (Terminal Consciousness)
                if (time.time() - self.last_market_pulse > 900) or ("STRONG" in btc_bias):
                    self.scanner.log_market_pulse("BTC/USD")
                    self.last_market_pulse = time.time()
                
        except Exception as e:
            logger.error(f"💥 Cycle Crash: {e}", exc_info=True)
            log_system_event("LocalRunner", str(e), level="ERROR")
            send_system_error("Local Runner", str(e))

    def _sync_trade_journal(self, live_equity):
        try:
            open_positions = self.tl.get_open_positions()
            history = self.tl.get_recent_history(hours=720)
            update_sync_state(live_equity, len(history) + len(open_positions))
            conn = get_db_connection()
            c = conn.cursor()
            for t in open_positions:
                c.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, price, status, ai_grade, mentor_feedback, strategy) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 0.0, 'Synced Active Trade', 'SYSTEM') ON CONFLICT(trade_id) DO UPDATE SET pnl = excluded.pnl, status = 'OPEN'", (t['entry_time'], t['id'], t['symbol'], t['side'], t['pnl'], t['price']))
            for t in history:
                c.execute("INSERT INTO journal (timestamp, trade_id, symbol, side, pnl, price, status, ai_grade, mentor_feedback, strategy) VALUES (?, ?, ?, ?, ?, ?, 'CLOSED', 0.0, 'Synced History', 'FLEET_SYNC') ON CONFLICT(trade_id) DO UPDATE SET pnl = excluded.pnl, status = 'CLOSED'", (t['close_time'], t['id'], t['symbol'], t['side'], t['pnl'], t.get('price', 0)))
                # Release correlation slot if this was a system trade
                if self.ledger:
                    sig_id = self.ledger.get_signal_id_by_trade_id(t['id'])
                    if sig_id:
                        self.corr_gate.release(sig_id)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"⚠️ Watchdog Sync Failed: {e}")

    def _audit_rogue_ledger(self):
        try:
            conn = get_db_connection()
            rogues = conn.execute("SELECT trade_id, symbol, side, pnl FROM journal WHERE status = 'CLOSED' AND strategy = 'ROGUE' AND date(timestamp) >= date('now', '-2 days')").fetchall()
            conn.close()
            import sqlite3 as _sqlite3
            ledger_conn = _sqlite3.connect(self.ledger.db_path if hasattr(self.ledger, 'db_path') else Config.DB_PATH)
            for r in rogues:
                if not ledger_conn.execute("SELECT signal_id FROM signed_ledger WHERE trade_id = ?", (str(r[0]),)).fetchone():
                    self.ledger.flag_rogue(trade_id=str(r[0]), notes=f"Audit mismatch: {r[1]}")
                    self.notifier._send_message(f"🚨 <b>ROGUE TRADE DETECTED</b>\nSymbol: <code>{r[1]}</code>\nID: <code>{r[0]}</code>\nPnL: <code>{r[3]:+.2f}</code>")
            ledger_conn.close()
        except Exception as e:
            logger.error(f"[RogueLedgerAudit] Error: {e}")

    def _trigger_alpha_interview(self, trade):
        self.notifier._send_message(f"🚀 *ALPHA DETECTED: Profitable Discretionary Trade*\n`{trade['symbol']}` win: `+${trade['pnl']}`.\nWhy?")
        self.awaiting_alpha_interview = True
        self.interview_trade_id = trade['id']
        self.last_interview_prompt_time = int(time.time())

    def _send_market_pulse(self, symbol, bias_str):
        try:
            df_4h = self.scanner.fetch_data(symbol, '4h', limit=100)
            if df_4h is not None:
                os.makedirs("data/charts", exist_ok=True)
                path = f"data/charts/pulse_{int(time.time())}.png"
                from src.engines.visualizer import generate_bias_chart
                generate_bias_chart(df_4h, symbol, timeframe="4h", output_path=path)
                self.notifier.send_photo(path, caption=f"📊 *BAYESIAN PIVOT MARKET PULSE*\n`{symbol}` Bias: `{bias_str}`")
        except: pass

    def main_loop(self):
        logger.info("⚙️ Bayesian Pivot Local Runner Initialized.")
        while self.running:
            self.run_cycle()
            next_scan = time.time() + (Config.get('RUN_INTERVAL_MINS', 3) * 60)
            while time.time() < next_scan and self.running:
                self._handle_commands()
                time.sleep(5)

if __name__ == "__main__":
    from src.core.process_lock import MasterProcessLock

    with MasterProcessLock(runner_name="LocalScannerRunner"):
        runner = LocalScannerRunner()
        runner.main_loop()
