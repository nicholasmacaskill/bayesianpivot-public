# AGENTS.md — Bayesian Pivot Trading Rules & Architectural Constraints

## CRITICAL: Execution & Broker Safety Protocols (TradeLocker / Upcomers)

### 1. Position Bracket Modifications (Stop Loss / Take Profit)
* **RULE:** Modifying Stop Loss (SL) or Take Profit (TP) on an open position **MUST ALWAYS** use the in-place `PATCH` endpoint:
  `PATCH /backend-api/trade/accounts/{account_id}/positions/{position_id}`
  Payload:
  ```json
  {
    "stopLoss": <float>,
    "stopLossType": "absolute",
    "takeProfit": <float>,
    "takeProfitType": "absolute"
  }
  ```
* **STRICT PROHIBITION:** **NEVER** submit a `POST /backend-api/trade/accounts/{account_id}/orders` with `type: "stop"` to update an existing position's stop loss. On TradeLocker, this creates a standalone conditional pending order. In hedging accounts, when the price hits the level, it triggers both orders: closing the initial position and opening a duplicate opposing naked position.

### 2. Position Termination (Closing Positions)
* **RULE:** Closing open positions **MUST ALWAYS** use the dedicated `DELETE` endpoint:
  `DELETE /backend-api/trade/accounts/{account_id}/positions/{position_id}`
* **STRICT PROHIBITION:** **NEVER** attempt to flatten an account by firing opposing market orders via `POST /orders`. This opens hedging positions rather than netting to flat.

### 3. Mandatory Use of Standardized Client Methods (No Raw HTTP Scripts)
* **RULE:** **NEVER** write raw `requests.post()` calls to `/orders` inside scratch scripts or one-off tools.
* All trade operations across all scripts, runners, and manual helpers **MUST** strictly invoke the validated methods in `src/clients/tl_client.py`:
  * `TradeLockerHelper.modify_position_bracket(pos_id, stop_loss, take_profit)`
  * `TradeLockerHelper.close_position(pos_id)`
  * `TradeLockerClient.update_fleet_stop_loss(new_stop_loss, symbol)`
  * `TradeLockerClient.close_all_fleet_positions()`
  * `TradeLockerClient.execute_trade_across_all_accounts(...)`

### 4. Mandatory Post-Execution Reconciliation
* **RULE:** After executing any fleet-wide operation (opening, updating, or closing positions), the agent or runner **MUST** immediately query `GET /positions` and `GET /orders` across all accounts to verify:
  1. Position count matches expected state on 100% of accounts.
  2. Zero stray or orphan pending stop/limit orders remain on any account book.
  3. Every active position has an attached, verified Stop Loss.

### 5. Multi-Account Rate-Limit Pacing & Anti-Desynchronization
* **RULE:** Multi-account fleet operations must enforce **2.0s to 2.5s adaptive pacing** between accounts.
* If any single account fails or receives an HTTP 429, the system must retry or handle the specific failure without leaving partial, unmanaged positions or cross-account direction mismatches.

### 6. Mandatory Protective Brackets on Entry (Zero Naked Trades)
* **RULE:** No order may ever be submitted with `stop_loss=None`. Every trade entry must have a mathematically calculated Stop Loss attached at the exact moment of order placement.

### 7. Upcomers 20% Consistency Rule & Fleet-Wide Payout Target Protocol
* **RULE:** The **20% Consistency Rule** mandates that **no single trading day may account for more than 20.0% of total cumulative net profit** at the time of payout request.
  * Formula: Daily Profit <= 0.20 * Total Target Profit
  * Payout eligibility requires at least 5 distinct profitable trading days; the system targets **15 to 25 consistent sessions** to guarantee spotless prop audit compliance.

* **FLEET TARGET & CONSISTENCY ALLOCATION:**
  * **Hard Payout Threshold Rule:** Payouts are strictly locked until the full balance target is achieved (**$27,000.00** on $25k accounts; **$54,000.00** on $50k accounts). Zero partial or intermediate withdrawals are permitted prior to reaching the full milestone.
  * **$25k Tier (Accounts 1, 3, 7):**
    * Target Net Profit: **+$2,000.00** (Payout Unlock Balance: **$27,000.00**).
    * Max Single-Day Profit: **$400.00** (20% ceiling; system clamped to **$380.00**).
    * Drawdown Floor Rules:
      * **Account 1 (`s79qv3xetj`):** High-Water Mark exceeded $26,000 -> Floor permanently locked at **$25,000.00** Even Equity. Base Risk: **$35.00** (scales to $50 at buffer > $600, $70 at buffer > $1,200).
      * **Account 7 (`875do5esrd`):** 5% Trailing Floor at **$23,750.00**. Base Risk: **$35.00**.
      * **Account 3 (`q20gxm287x`):** 5% Trailing Floor at **$23,750.00**. Base Risk: **$50.00**.
  * **$50k Tier (Accounts 2, 6, 9):**
    * Target Net Profit: **+$4,000.00** (Payout Unlock Balance: **$54,000.00**).
    * Max Single-Day Profit: **$800.00** (20% ceiling; system clamped to **$760.00**).
    * Drawdown Floor Rules:
      * **Account 9 (`jfcuue7er3` - Oracle Lead Striker):** 5% Trailing Floor at **$47,500.00**. Base Risk: **$100.00** (scales to $120 after ~2 wins / cushion >= +$350 above $50k).
      * **Account 2 (`498svcbpfi`):** 5% Trailing Floor at **$47,500.00**. Base Risk: **$70.00** (scales to $120 at buffer > $1,800).
      * **Account 6 (`dwundrtxjv`):** 5% Trailing Floor at **$47,500.00**. Base Risk: **$80.00** (scales to $120 at buffer > $1,800).
  * **$10k Tier (Accounts 4, 5, 8):**
    * **Permanently Decommissioned / Liquidation-Only.** Sizing set to **$0.00** (quarantined). Floor: $9,500.00.
  * **Live Broker Verification Command:**
    * Real-time balances, true buffers, and loss runways must be queried live via:
      `python3 scripts/get_fleet_vitals.py` (or `--json` for structured payload).

### 8. Strict Communication & Output Formatting Protocols (No LaTeX / Clean English)
* **RULE:** **NEVER** use LaTeX math formatting, dollar sign equation delimiters (`$...$`, `$$...$$`), or raw equation markup (`\ge`, `\le`, `\to`, `\times`, `\text{...}`, `\frac`) in agent responses or documentation.
* **MANDATORY STYLE:**
  - Always write clean, plain English and standard keyboard characters.
  - Write `>=` or "greater than or equal to" (never `$\ge$`).
  - Write `<=` or "less than or equal to" (never `$\le$`).
  - Write `->` or "to" (never `$\to$`).
  - Write `*` or `x` (never `$\times$`).
  - Write `+2.8R`, `-1.0R`, `1.5R` as plain text (never `$+2.8\text{R}$`).
  - Write simple formulas in plain text: `Daily Profit <= 0.20 * Total Profit` or `MFE = (Peak - Entry) / Risk`.
  - Keep all tables, bullet points, numbers, and summaries crisp, plain, and readable.

### 9. Mandatory Adversarial Quality Process & Git Synchronization Protocol
* **RULE:** Any time a change is made to the working live codebase, it **MUST** be run through the full adversarial quality verification process to ensure zero regressions and guarantee the code works correctly in production as intended:
  1. **Automated Invariant & Regression Testing:** Run targeted unit tests and the Sovereign 3D Crucible Matrix (`tests/run_3d_crucible.py` / `tests/run_bulletproof_harness.py` / `PYTHONPATH=. ./venv/bin/pytest`) to mathematically verify all broker, risk, sizing, and firewall constraints pass.
  2. **Production Integrity Check:** Confirm runtime daemon status, log outputs, and invariant sentry health to ensure production stability.
  3. **Immediate Commit & Remote Push:** Once each change passes the adversarial quality verification, immediately commit with a clean, descriptive message and push the updated code to the private repository (`gitlab main`).

### 10. Mandatory Live Broker Query Protocol (Strict Prohibition Against Stale Hardcoded Balances)
* **RULE:** Agents and runners **MUST NEVER** store, quote, or rely on mutable live account balances, drawdown buffers, or loss runways as static text inside `AGENTS.md` or any documentation file.
* **MANDATORY EXECUTION:** Whenever account balances, equity, true drawdown buffers, or loss runways are required for decision-making, sizing, or reporting, the agent/runner **MUST** query the live TradeLocker broker API directly:
  - CLI: `python3 scripts/get_fleet_vitals.py` (or `--json`)
  - Code: `from scripts.get_fleet_vitals import get_live_fleet_vitals` or `TradeLockerClient.get_account_details()`
* `AGENTS.md` strictly documents immutable architectural rules, formulas, tiers, and floor logic—NOT decaying snapshot numbers.

### 11. Asset Multiplier & Contract Size Invariant (No Crypto-Only Assumptions)
* **RULE:** Never compute lot sizing, position value, or projected profit using naive unit formulas (`lots = risk / stop_dist` or `pnl = lots * (exit - entry)`).
* **MANDATORY EXECUTION:** Every lot calculation, notional cap check, and PnL calculation across all scripts, scanners, and runners **MUST** incorporate `Config.get_contract_size(symbol)`:
  - Crypto (BTC, ETH, SOL): `contract_size = 1.0` (1 lot = 1 coin)
  - Gold (XAU/USD): `contract_size = 100.0` (1 lot = 100 troy ounces)
  - Silver (XAG/USD): `contract_size = 5000.0` (1 lot = 5,000 troy ounces)
  - Forex pairs (EUR, GBP, etc.): `contract_size = 100000.0` (1 lot = 100k units)
* **STRICT PROHIBITION:** Omitting `contract_size` on Gold distorts lot sizing by 100x and produces 100x errors in the Upcomers 20% consistency ceiling check.

### 12. Immutable Trade Measurement Basis (The Ruler Invariant)
* **RULE:** Stop Loss defense mechanisms (Stepped Defense at +1.0R, Break-Even trail at +1.5R) tighten the live position's Stop Loss level over time. Agents and watchdogs **MUST NEVER** recompute the trade's risk denominator using the current mutated Stop Loss.
* **MANDATORY EXECUTION:** The initial risk basis (`initial_sl`, `initial_risk_usd`, `initial_r_dist`) **MUST** be captured and persisted on the very first observation of the trade.
* All downstream R-multiples, MFE peak calculations, and defense triggers must strictly divide by `initial_risk_usd`. Tightening a Stop Loss must never artificially inflate the trade's reported R-multiple.

### 13. End-to-End String & Enum Contract Synchronization
* **RULE:** When adding, renaming, or expanding session identifiers, pattern names, or trade verdicts in any module, the agent **MUST** grep and synchronize all downstream consumer lookup tables in the same atomic commit.
* **MANDATORY PRACTICES:**
  - If a session is added (e.g. `LONDON_CLOSE_NY_MORNING` or `ASIAN_SESSION_JUDAS`), verify that `is_gold_liquid_session`, `session_restriction` allowed lists, and firewall killzone gates all recognize the exact string.
  - If a scanner verdict is stored (e.g. `CONFIRMED`, `FLOW_GO`), verify that the watchdog or recovery queries look for the matching string (`.in_("verdict", ["CONFIRMED", "ACCEPTED", "FLOW_GO"])`), not a stale assumption like `ACCEPTED`.
  - All string comparisons on symbols, sessions, and sides must be normalized: `sym.replace("/", "").replace("_", "").upper()`.

### 14. Strict Asset-Scoped News Filtering (Zero Foreign Currency Blackouts)
* **RULE:** Macroeconomic calendar filters must only block trades if the high-impact event currency matches the target asset's base or quote currency, or is a verified global macro event (FOMC, Fed Decisions, Jerome Powell speeches, Global Interest Rate decisions).
* **STRICT PROHIBITION:** Foreign currency events (AUD, CAD, JPY, GBP, NZD) must **NEVER** lock out USD-denominated trades (`BTC/USD`, `XAU/USD`).
* Any wrapper method (e.g. `ExecutionFirewall.check_news_calendar()`) **MUST** accept and pass `symbol` down to `CalendarFilter.is_safe_to_trade(symbol=symbol)`.

### 15. Broker ID Resiliency & Bidirectional Position Matching
* **RULE:** TradeLocker position lookups and watchdog position defense must **NEVER** rely solely on string symbol matching (`symbol in pos['symbol']`).
* If an instrument ID mapping is delayed or unmapped by the broker API, TradeLocker returns raw numeric IDs (e.g. `"19915"`).
* **MANDATORY EXECUTION:** Position matching must always evaluate both symbol and tradable instrument ID:
  `is_match = (target_sym in pos_sym or pos['tradableInstrumentId'] == target_inst_id)`
* In dictionary lookups (e.g. `resolve_instrument_id`), exact dictionary key match (`norm in cache`) **MUST** be checked first before running substring comparisons. Fall back to `TradeLockerHelper._shared_instruments_cache` if instance cache is uninitialized.

### 16. Zero Silent Exception Swallowing & Clean Network Logging
* **RULE:** Never use bare `except Exception: pass` or `except: return False` on core business logic without structured logging, and never reference unimported packages (e.g. `logger` without `import logging`) inside exception handlers.
* Transient network timeouts (`ReadTimeout`, `ConnectTimeout`, `ConnectionReset`) from public exchanges or broker REST endpoints **MUST** be logged as clean warnings without dumping raw 15-line stack traces so that the `QualityGovernor` silent error sentry does not trigger false alarm escalations.

### 17. Adversarial Negative-Boundary Testing Protocol
* **RULE:** When adding unit or integration tests for new features, agents must NOT only test the "happy path" or positive trigger. Every test suite **MUST** include negative boundary tests:
  - Macro News: Test that FOMC blocks USD trades, AND assert that AUD news does NOT block BTC/USD.
  - Killzones: Test that off-hours are blocked, AND assert that `LONDON_CLOSE_NY_MORNING` passes for Gold.
  - Dynamic Defense: Test that moving SL from -1.0R to -0.3R at +1.0R keeps the risk denominator invariant.
  - Multi-Asset Multipliers: Run the same lot sizing test across BTC (`contract_size = 1.0`) and Gold (`contract_size = 100.0`) to catch 100x discrepancies.

---

### Incident Post-Mortem Reference
* Full forensic documentation: `docs/INCIDENT_2026-08-26_TRADELOCKER_STOP_ORDER_DUPLICATION.md`
* 24-Bug Adversarial Forensic Audit: `.gemini/antigravity-ide/brain/4da29527-d048-4245-855c-efbf1b90edc7/walkthrough.md`


