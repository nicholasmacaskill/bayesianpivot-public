import os

# Configure yfinance to use a local writable directory inside the workspace for its cache
try:
    import yfinance.cache as yf_cache
    workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    workspace_cache = os.path.join(workspace_dir, "data", "yfinance_cache")
    os.makedirs(workspace_cache, exist_ok=True)
    yf_cache.set_cache_location(workspace_cache)
    yf_cache.set_tz_cache_location(workspace_cache)
    
    # Fallback to dummy caches to completely disable disk activity
    yf_cache._CookieCacheManager._Cookie_cache = yf_cache._CookieCacheDummy()
    yf_cache._ISINCacheManager._isin_cache = yf_cache._ISINCacheDummy()
    yf_cache._TzCacheManager._tz_cache = yf_cache._TzCacheDummy()
except Exception:
    pass


class Config:
    # Target Instruments
    SYMBOLS = ['BTC/USD', 'XAU/USD']             # BTC + Gold Active (Live Fleet Execution - Alpha Champions)
    SHADOW_SYMBOLS = ['ETH/USD', 'SOL/USD']      # 100% Zero-Risk Shadow Tracking (A/B Tournament Lab)
    ALT_SYMBOLS = ['ETH/USD', 'SOL/USD']
    
    TIMEFRAME = '5m'
    HTF_TIMEFRAME = '1h'
    
    # Risk Management
    RISK_PER_TRADE = 0.007  # 0.7% (Default)
    FIXED_RISK_USD = 75.0   # Quantitative Sweet Spot: $75 per trade (0.30% on $25k)
    MAX_RISK_USD = 150.0    # Strict absolute risk cap per trade
    MAX_PROFIT_USD = 400.0  # Strict absolute profit cap per trade
    MAX_NOTIONAL_VALUE_USD = 50000.0  # Hard cap: max position value per trade
    MIN_STOP_LOSS_ATR = 2.0           # Minimum stop loss distance (2.0x ATR multiplier for spread immunity)
    # Minimum stop distance per asset as % of price (prevents micro-spread stop runs)
    MIN_STOP_PCT = {
        "BTC/USD": 0.003,   # 0.30% = ~$250 at $83k
        "ETH/USD": 0.0035,  # 0.35% = ~$8.70 at $2,480 (Immune to broker spread micro-wicks)
        "XAU/USD": 0.003,   # 0.30% = ~$7.20 at $2,400
        "SOL/USD": 0.0035,  # 0.35% = ~$0.45 at $130
    }
    # Minimum target distance per asset as % of price (ensures 2.5R+ intraday targets pass)
    MIN_TARGET_PCT = {
        "BTC/USD": 0.005,   # 0.5% = ~$320 at $64k
        "ETH/USD": 0.005,   # 0.5% = ~$17.50 at $3,500
        "XAU/USD": 0.005,   # 0.5% = ~$12.00 at $2,400
        "SOL/USD": 0.008,   # 0.8% = ~$1.20 at $150
    }

    MAX_POSITION_SIZES = {
        "BTC/USD": 0.25,    # Capped at 0.25 BTC
        "ETH/USD": 27.0,    # ~$49k notional at $1,800
        "XAU/USD": 5.0,     # Capped at 5.0 Lots Gold
        "SOL/USD": 416.0,   # ~$50k notional at $120
    }

    # ── Fleet-Wide Concurrency, Anti-Stacking & Cooldown Safety Gates ──
    MAX_CONCURRENT_FLEET_POSITIONS = 2  # Hard cap: Max 2 concurrent open positions across the entire fleet
    MAX_POSITIONS_PER_ACCOUNT = 2       # Strict Hard Cap: No individual account can ever have more than 2 open position tickets
    MAX_CONCURRENT_PER_SYMBOL = 1       # Zero stacking: Max 1 position per asset across the entire fleet
    SYMBOL_COOLDOWN_MINUTES = 30        # Mandatory 30-minute persistent debounce cooldown per symbol
    MAX_LOT_SIZE_PER_ORDER = {
        "BTC/USD": 0.60,    # Max 0.60 BTC lots per order (Allows full $100-$120 risk on tight sniper stops)
        "ETH/USD": 5.0,     # Max 5.00 ETH lots per order (Prevents 15+ lot spikes)
        "XAU/USD": 2.0,     # Max 2.00 Gold lots per order
        "SOL/USD": 50.0,    # Max 50.0 SOL lots per order
    }

    MAX_DRAWDOWN_LIMIT = 0.05    # 5.0% Total Trailing Drawdown Lockout (Strict Hard Ceiling)
    DAILY_DRAWDOWN_LIMIT = 0.015 # 1.5% Daily Drawdown Lockout (Ultra-Defensive)

    # ── Emergency Drawdown Protection & Quarantine ──
    # These accounts are already LIQUIDATION_ONLY at the broker level; listed here as a belt-and-suspenders guard.
    EMERGENCY_LOCKOUT_ACCOUNTS = [
        "h4sj53tg4f@upcomers.com",  # Account 8: LIQUIDATION_ONLY
        "hnr10rtj4k@upcomers.com",  # Account 5: LIQUIDATION_ONLY
        "vkrbpwdprh@upcomers.com",  # Account 4: LIQUIDATION_ONLY
    ]

    # ── Tier-Specific Dollar Risk Ceilings (Distance-to-Default Protected) ──
    TIER_CAPS_ENABLED = True

    MAX_CONSECUTIVE_DAILY_LOSSES = 3    # Hard Circuit Breaker: Max 3 consecutive full losses (3.0 units) per day before 24h lockout
    DAILY_TRADE_LIMIT = 3.0             # Upgraded to 3.0 Risk Units per day (Fractional Risk-Budgeted Accounting)
    DAILY_RISK_UNIT_CAP = 3.0           # Total Daily Capacity: 3.0 Risk Units per day (allows 2 probes + 2 full trades)
    DAILY_LOSS_UNIT_CIRCUIT_BREAKER = 3.0 # 3.0 Unit Cumulative Realized Loss Circuit Breaker (3 full losses or 6 probe losses)
    MAX_SETUPS_PER_KILLZONE_SESSION = 1 # Session Anti-Clustering: Max 1 setup per killzone session (Asian, London, NY)
    JUDAS_PROBE_RISK_SCALE = 0.50       # Half-size probe risk scale (0.5 Units)
    TARGET_RR = 2.5                     # 2.5R Target (Peak mathematical expectancy pocket: +0.75R/trade)

    # ── Strategy 9: Judas Inducement Hunter (GRADUATED CHAMPION: 80% WR / 10.0 PF) ───
    STRATEGY_9_ENABLED = True
    STRATEGY_9_MIN_WICK_PCT = 60.0      # 60% Minimum Rejection Wick (Graduated from Challenger)
    STRATEGY_9_MIN_ATR_MULT = 1.5       # 1.5x 20-period ATR Range (Graduated from Challenger)
    STRATEGY_9_MIN_VOL_MULT = 1.5       # 1.5x 20-period Average Volume (Graduated from Challenger)
    STRATEGY_9_TARGET_RR = 2.5          # 2.5R Target (Calibrated to institutional liquidity pocket)
    STRATEGY_9_STOP_BUFFER_ATR = 0.35   # 0.35 ATR stop buffer for breathing room
    STRATEGY_9_RISK_USD = 40.0          # Base dollar risk per trade ($40 on $25k, $80 on $50k)
    STRATEGY_9_BYPASS_GENERIC_AI = False # Requires full 8.0+ AI Validator gate
    STRATEGY_9_AUTO_EXECUTE = True      # ✅ GRADUATED: Live Fleet Auto-Execution Enabled

    # ── Strategy 5: XAU/USD 50% Consequent Encroachment Longs (GRADUATED CHAMPION: 65.4% WR / 4.72 PF) ───
    STRATEGY_5_ENABLED = True
    STRATEGY_5_CE_FILL_PCT = 0.50       # Exact 50% midpoint of 1H/15m FVG
    STRATEGY_5_TARGET_RR = 2.5          # 2.5R Target (Calibrated to peak mathematical expectancy)
    STRATEGY_5_STOP_BUFFER_ATR = 0.25   # 0.25 ATR stop buffer below FVG bottom
    STRATEGY_5_RISK_USD = 40.0          # Base dollar risk per trade ($35 on $25k, $70 on $50k)
    STRATEGY_5_AUTO_EXECUTE = True      # ✅ GRADUATED: Live Fleet Auto-Execution Enabled (Weekdays Only, Gold Longs Only)
    STRATEGY_5_LONGS_ONLY = True        # Strict Directional Gate: Never trade CE shorts (12.8% toxic failure)
    STRATEGY_5_REQUIRE_HTF_BULLISH = True # Mandatory 4H/Daily macro structural alignment

    # ── Strategy Auction Market: Anchored VWAP 2-Sigma Snapbacks & Wyckoff VSA (GRADUATED PROBE) ──
    AUCTION_MARKET_AUTO_EXECUTE = True          # ✅ GRADUATED: Live Fleet 0.5x Probe Auto-Execution
    AUCTION_MARKET_PROBE_RISK_SCALE = 0.50      # 0.5x probe risk ($17.50 on $25k, $35.00 on $50k)

    # Prop Firm Execution Profiles
    ACTIVE_FIRM = "UPCOMERS"
    
    PROP_FIRMS = {
        "UPCOMERS": {
            "name": "Upcomers", 
            "url": "https://upcomers.com/faq", 
            "contract_size": 0.03, 
            "commission_rate": 0.005,
            "desc": "High Leverage / Micro Lots (Adversarial)"  
        },
        "FTMO": {
            "name": "FTMO", 
            "url": "https://ftmo.com/en/faq/", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "The Industry Standard (Safe)"
        },
        "FUNDEDNEXT": {
            "name": "FundedNext", 
            "url": "https://fundednext.com/faq", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "Balanced Growth"
        },
        "TOPSTEP": {
            "name": "TopStep", 
            "url": "https://intercom.help/topstep-llc/en/", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "Futures Focus"
        },
        "ALPHA_CAPITAL": {
            "name": "Alpha Capital", 
            "url": "https://alphacapitalgroup.uk/faq/", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "No Time Limits"
        },
        "THE5ERS": {
            "name": "The5ers", 
            "url": "https://the5ers.com/faqs/", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "Instant Funding"
        },
        "BLUE_GUARDIAN": {
            "name": "Blue Guardian", 
            "url": "https://blueguardian.com/faq", 
            "contract_size": 1.0, 
            "commission_rate": 0.001,
            "desc": "Low Slippage"
        },
        "E8_MARKETS": {"name": "E8 Markets", "url": "https://e8markets.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "LARK_FUNDING": {"name": "Lark Funding", "url": "https://larkfunding.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "FUNDING_PIPS": {"name": "Funding Pips", "url": "https://fundingpips.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "GOAT_FUNDED": {"name": "Goat Funded", "url": "https://goatfundedtrader.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "INSTANT_FUNDING": {"name": "Instant Funding", "url": "https://instantfunding.io/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "MAVEN": {"name": "Maven Trading", "url": "https://maventrading.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "AULA_FLOW": {"name": "AquaFunded", "url": "https://aquafunded.com/faq", "contract_size": 1.0, "commission_rate": 0.001},
        "MENT_FUNDING": {"name": "Ment Funding", "url": "https://mentfunding.com/faq", "contract_size": 1.0, "commission_rate": 0.001}
    }
    
    USE_TRADELOCKER_API = True
    SYNC_AUTH_KEY = os.environ.get("SYNC_AUTH_KEY", "")
    
    # ── TWO-TRANCHE "PROBE & SCALE" AUTO-EXECUTION ENGINE ──
    LIVE_AUTO_EXECUTION = True                    # ARMED: Live Fleet Auto-Execution Active
    AUTO_PROBE_RISK_SCALE = 1.00                  # 100% full lot size entry with structural stop width
    SCALE_IN_TRANCHE_2_SCALE = 0.00               # Scale-outs used instead of scale-ins
    AUTO_EXECUTION_MIN_SCORE = 8.0                # Minimum AI conviction score required for auto-execution

    # Sovereign Light Simplification Toggles (Defaults to simplified mode)
    BYPASS_AI_GATE = False        # Set to True to execute setups purely on Gates 1-5
    BYPASS_BIOMETRIC_GATE = True  # Set to True to ignore biometric stress halts
    
    # Target Profit Mode (Direct $300-$400 Clocking)
    TARGET_PROFIT_MODE = False     # Set to True to scale risk specifically for a target profit
    TARGET_PROFIT_USD = 350.0      # Target profit per trade (e.g. $300 - $400)

    # Correlation & Calendar
    CORRELATION_MAX_PER_DIRECTION = 1
    CORRELATION_SLOT_EXPIRY_HRS   = 4
    CALENDAR_BLACKOUT_MINUTES = 30
    NY_LUNCH_BLACKOUT = (17, 18)

    # Regime Filter
    REGIME_BLOCK_CHOPPY   = True
    REGIME_ADX_TREND_MIN  = 20
    REGIME_ADX_CHOPPY_MAX = 18

    # Signed Trade Ledger
    LEDGER_ENABLED = True

    # Automated Retraining
    RETRAIN_ENABLED          = True
    RETRAIN_MIN_SAMPLES      = 5
    RETRAIN_EXPORT_JSONL     = True
    
    # Strategy Mode
    STRATEGY_MODE = "VOLUME_OPERATOR"
    VARIANT_SIZING_SHADOW_MODE = True  # Locks live fleet to Flat 1.0x Base Risk; tests dynamic/variant multipliers in shadow tournament
    AI_THRESHOLD_LONG = 8.0   # Leveled to match shorts (data shows longs avg $360 win vs $221)
    AI_THRESHOLD_SHORT = 8.0
    LONG_RISK_MULTIPLIER = 1.0  # Full size — longs earn it (55.1% win rate, +$5,849 net)
    AI_THRESHOLD = 8.5
    AI_THRESHOLD_ASIAN_FADE = 7.5
    OFF_HOURS_RISK_MULTIPLIER = 0.5
    TRANSITION_RISK_MULTIPLIER = 0.5
    
    # ── 98% Reliability Standard Thresholds ──────────────────────
    SYNC_PRICE_DELTA_MAX = 0.005     # 0.5% (Loosened for crypto volatility)
    SYNC_LATENCY_SEC_MAX = 120        # 2 Minutes
    SLIPPAGE_ATR_RATIO_MAX = 1.5      # Spread / ATR(14)
    HURST_CHAOS_RANGE = (0.42, 0.55)  # Must be rejected as CHOP / RANDOM
    HURST_MIN_MEMORY = 0.55          # Trending threshold
    HURST_MAX_RANDOM = 0.42          # Mean-reverting threshold (Tightened from 0.45 to prevent borderline chop)
    
    # ── AI Risk Logic & Tiered Profit Protection ─────────────
    ROI_OPTIMIZATION_ENABLED = True
    TP1_RATIO = 0.5                    # 50% scale out at TP1
    STEPPED_DEFENSE_ENABLED = True     # Tier 0.5: Stepped Risk Reduction at +1.0R
    STEPPED_DEFENSE_TRIGGER_R = 1.0    # Trigger at +1.0R floating gain
    STEPPED_DEFENSE_LOCKED_R = -0.3    # Cut Stop Loss to -0.3R (saving 70% of loss if reversed)
    BE_TRIGGER_R = 1.5                 # Tier 1: Move SL to entry (Break-Even) at +1.5R
    BE_OFFSET_R = 0.08                 # Commission & Spread Offset (True Net Break-Even: +0.08R guarantees $0.00 net after broker fees)
    BE_MIN_OFFSET_USD = {              # Minimum dollar price buffer per asset to cover round-trip broker commission + spread
        "BTC": 25.0,                   # $25.00 on BTC price (~$2.50 net on 0.10 lot)
        "ETH": 2.0,                    # $2.00 on ETH price
        "SOL": 0.20,                   # $0.20 on SOL price
        "XAU": 0.80                    # $0.80 on Gold price
    }
    TIER2_LOCK_TRIGGER_R = 2.5         # Tier 2: Trigger lock when trade reaches +2.5R
    TIER2_LOCK_LOCKED_R = 1.0          # Lock in +1.0R guaranteed profit
    TIER2_TP_PCT_TRIGGER = 0.80        # >= 80% TP distance also triggers Tier 2 lock
    SESSION_TRANSITION_PROTECT_ENABLED = True  # Protect Asian positions before London Open
    SESSION_TRANSITION_MIN_R = 1.0     # Minimum +1.0R floating to lock BE at session transition
    AI_MIN_SMT_CONVERGENCE = 0.7       # Threshold for "Institutional Convergence"

    # ── Split-Fleet Barbell Architecture ──────────────────────
    SPLIT_FLEET_SCALE_OUT_ENABLED = True
    # Accounts that scale out 50% @ +1.5R to build realized cash balance:
    # Index 0: Account 1 ($25k Payout Account)
    # Index 2: Account 3 ($25k Account)
    # Index 3: Account 4 ($10k Account)
    # Index 4: Account 5 ($10k Account)
    # Index 8: Account 9 ($50k Oracle Lead Striker)
    SCALE_OUT_ACCOUNT_INDICES = [0, 2, 3, 4, 8]
    
    # Accounts that run 100% full position with Trailing Ratchet for max multi-R windfalls:
    # Index 1: Account 2 ($50k Account)
    # Index 5: Account 6 ($50k Account)
    # Index 6: Account 7 ($25k Account)
    # Index 7: Account 8 ($10k Account)
    FULL_RUNNER_ACCOUNT_INDICES = [1, 5, 6, 7]
    
    SCALE_OUT_TP1_R = 2.0           # Take Profit 1 level = +2.0R (Expands Realized R:R)
    SCALE_OUT_TP1_PCT = 0.50        # 50% size closed at TP1
    TP_FRONT_RUN_CUSHION_ENABLED = True # Micro-buffer on limit TPs to front-run stop clusters
    TP_FRONT_RUN_CUSHION_USD = {"BTC": 15.0, "ETH": 1.0, "SOL": 0.10, "XAU": 0.50}
    
    # ── Max Favorable Excursion (MFE) Peak Retracement Ratchet ──
    MFE_PEAK_RATCHET_ENABLED = True        # ✅ ENABLED: Position-isolated R-multiple guarantees MFE only triggers if genuine peak >= +2.0R
    MFE_MIN_PEAK_R = 2.0            # Minimum peak R required to arm the ratchet
    MFE_MAX_RETRACEMENT_R = 0.75    # Giving back 0.75R from peak triggers defensive scale-out
    
    # ── Pre-Macro Event Defense ──
    MACRO_DEFENSE_ENABLED = True    # Front-run high-impact macro data if in profit
    MACRO_DEFENSE_MIN_R = 1.0       # Minimum floating R to bank before macro event
    MACRO_DEFENSE_WINDOW_MINS = 15  # Pre-event protection window (minutes)
    
    # ── Tiered Risk Scaling (Dynamic Fractional Kelly Sizing) ──
    DYNAMIC_RISK_SCALING_ENABLED = False
    TIER_CAPS_ENABLED = True            # Enforce hard dollar risk ceilings per account tier
    TIER_MAX_RISK_10K = 0.0             # Quarantined: $0.00 risk on $10k accounts
    TIER_MAX_RISK_25K = 40.0            # Default $25k ceiling
    TIER_MAX_RISK_50K = 80.0            # Default $50k ceiling
    
    # ── Per-Account Calibrated Risk Ceiling (Decoupled by Buffer Runway) ──
    ACCOUNT_RISK_CAPS = {
        "jfcuue7er3@upcomers.com": 100.0, # Account 9: Fresh 50k Oracle Lead Striker ($100 base -> $120 after +$350 buffer -> $180 once floor locks at $50k; 25.0 losses runway on $2,500 cushion)
        "s79qv3xetj@upcomers.com": 35.0,  # Account 1: Accelerated $35.00 (8.2 losses runway on $286 buffer)
        "875do5esrd@upcomers.com": 35.0,  # Account 7: Balanced $35.00 (14.5 losses runway on $510 buffer)
        "q20gxm287x@upcomers.com": 50.0,  # Account 3: Growth $50.00 (17.3 losses runway on $865 buffer)
        "498svcbpfi@upcomers.com": 70.0,  # Account 2: Standard $50k $70.00 (15.2 losses runway on $1,067 buffer)
        "dwundrtxjv@upcomers.com": 80.0,  # Account 6: Standard $50k $80.00 (16.2 losses runway on $1,301 buffer)
        # Quarantined / Decommissioned Accounts:
        "vkrbpwdprh@upcomers.com": 0.0,   # Account 4 ($10k)
        "hnr10rtj4k@upcomers.com": 0.0,   # Account 5 ($10k)
        "h4sj53tg4f@upcomers.com": 0.0,   # Account 8 ($10k)
    }
    
    BASELINE_RISK_PCT = 0.0020          # 0.20% Defensive Base Risk (Lowered from 0.50% for DtD protection)
    A_PLUS_RISK_PCT = 0.0040            # 0.40% Scaled Risk (Lowered from 0.85% for A+ setups)
    A_PLUS_MIN_SCORE = 9.0              # AI Score threshold (>= 9.0 / 10.0 or >= 90 / 100)
    A_PLUS_REQUIRE_SMT = True           # Requires verified SMT divergence
    A_PLUS_REQUIRE_KILLZONE = True      # Requires London (07-10z) or NY (12-20z) Killzone
    A_PLUS_MIN_HURST = 0.58             # Requires trending regime (Hurst >= 0.58)
    MAX_SINGLE_TRADE_RISK_CEILING = 0.0050 # 0.50% Hard Safety Ceiling per trade (Halved from 1.00%)

    AI_TRUST_TIER_AGGRESSIVE = 90      # Score 90+ -> 0.85% - 1.0% Risk
    AI_TRUST_TIER_CONSERVATIVE = 75    # Score 75-89 -> 0.5% Risk
    AI_TRUST_TIER_MINIMUM = 75         # < 75 -> 0% Risk (Monitor)
    
    # ── Upcomers 20% Consistency & Payout Configuration ──
    CONSISTENCY_RULE_PCT = 0.20             # Max 20% of total profit allowed on any single trading day
    ACCOUNT_1_PROFIT_TARGET = 2000.0        # Account 1 Target Net Profit ($27,000 balance on $25k base)
    ACCOUNT_1_DAILY_PROFIT_CAP = 380.0      # Safety cap: Never exceed $380 in a single day (below the $400 20% threshold)
    TIER_MAX_DAILY_PROFIT_25K = 380.0       # 20% consistency ceiling on $25k tier (Target: $2,000)
    TIER_MAX_DAILY_PROFIT_50K = 760.0       # 20% consistency ceiling on $50k tier (Target: $4,000)
    ACCOUNT_1_MIN_TRADING_DAYS = 5          # Minimum trading days required for payout eligibility (target 15-25 days)
    # ──────────────────────────────────────────────────────────
    
    # Exit Parameters (Scalp Optimized)
    TP1_R_MULTIPLE = 1.0               # First scale out at +1.0R
    TP2_R_MULTIPLE = 3.0               # Runner target at +3.0R (Extends free-ride runner)
    STOP_LOSS_ATR_MULTIPLIER = 2.5
    ENTRY_OFFSET_ATR_MULTIPLIER = 0.5
    
    # Killzones (UTC)
    KILLZONE_ASIA = (0, 4)
    KILLZONE_ASIAN_FADE = (4, 7)
    KILLZONE_LONDON = (7, 10)
    KILLZONE_NY_CONTINUOUS = (12, 20)
    
    # Edge & Volume-Augmented Optimization
    MIN_SMT_STRENGTH = 0.25
    MIN_TARGET_PCT = 0.0035  # 0.35% minimum price distance on 5m charts (or >= 2.0R)
    MIN_PRICE_QUARTILE = 0.0
    MAX_PRICE_QUARTILE = 0.75
    MIN_PRICE_QUARTILE_SHORT = 0.25
    MAX_PRICE_QUARTILE_SHORT = 1.0
    
    # Dynamic Volume-Augmented Substitution Thresholds
    RVOL_EXPANSION_THRESHOLD = 2.5   # 2.5x volume relative to 20-period SMA
    CVD_EXHAUSTION_THRESHOLD = 0.70  # 70% delta decay/exhaustion on sweep
    DYNAMIC_FIB_SHALLOW = 0.382      # Shallow pullback level during high RVol
    DYNAMIC_FIB_DEEP = 0.705         # Standard ICT deep discount level
    SHADOW_UNIVERSE = ["BTC/USD", "ETH/USD", "SOL/USD"]
    
    # Database Path (Local vs Modal Volume)
    DB_PATH = "/data/smc_alpha.db" if os.path.isdir("/data") else os.path.join(os.getcwd(), "data", "smc_alpha.db")

    # Local Runner Parameters
    RUN_INTERVAL_MINS = 3

    @classmethod
    def get_contract_size(cls, symbol: str) -> float:
        """
        Returns the true broker contract size / lot multiplier for any asset.
        - Crypto (BTC, ETH, SOL, etc.): 1.0
        - Gold (XAU/USD): 100.0 (1 lot = 100 oz)
        - Silver (XAG/USD): 5000.0 (1 lot = 5000 oz)
        - Forex majors / minors: 100000.0 (1 lot = 100k units)
        """
        clean = str(symbol).replace("/", "").replace("_", "").upper()
        if any(c in clean for c in ["BTC", "ETH", "SOL", "CRYPTO", "GALA", "COIN"]):
            return 1.0
        elif any(m in clean for m in ["XAU", "GOLD"]):
            return 100.0
        elif any(m in clean for m in ["XAG", "SILVER"]):
            return 5000.0
        elif any(fx in clean for fx in ["EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF"]):
            return 100000.0
        return 1.0

    @classmethod
    def get(cls, key, default=None):
        return getattr(cls, key, default)

    @staticmethod
    def get_modal_secrets():
        import modal
        return [modal.Secret.from_name("smc-secrets")]
