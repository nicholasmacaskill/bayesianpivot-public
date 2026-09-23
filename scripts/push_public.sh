#!/usr/bin/env bash
# scripts/push_public.sh
# ─────────────────────────────────────────────────────────────────────────────
# Selective push to the public GitHub mirror.
# Only safe files (dashboard, stubs, harness, docs) are included.
# Core engine logic, config thresholds, and runner orchestration are excluded.
#
# Usage: bash scripts/push_public.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

REMOTE="public"
BRANCH="main"
TMP_BRANCH="public-staging-$(date +%s)"

echo "📦 Creating staging branch..."
git checkout -b "$TMP_BRANCH"

# ── Files to REMOVE from the public push ──────────────────────────────────────
REMOVE_FILES=(
  # Core IP — scanner logic
  "src/engines/smc_scanner.py"         # Replaced by stub in public mirror
  "src/engines/alpha_sweep_scanner.py"
  "src/engines/champion_challenger_lab.py"
  "src/engines/judas_inducement_engine.py"
  "src/engines/psychology_engine.py"
  "src/engines/execution_shadow_engine.py"
  "src/engines/biometric_engine.py"
  "src/engines/counterfactual_tracker.py"
  "src/engines/inducement_tracker.py"
  "src/engines/multi_account_funnel.py"
  "src/engines/qa_quant_agent.py"
  "src/sovereign_core/"
  "src/engines/sovereign_engine/patterns.py"
  "src/engines/sovereign_engine/scanners.py"
  "src/engines/sovereign_engine/analyzer.py"
  "src/engines/sovereign_engine/forensics.py"
  "src/engines/sovereign_engine/injection_defense.py"
  "src/engines/sovereign_engine/persistence.py"
  "src/engines/sovereign_engine/tripwire.py"
  "src/engines/sovereign_engine/triggers.py"

  # AI scoring & audit logic
  "src/engines/ai_audit_engine.py"
  "src/engines/retraining_loop.py"

  # Signal pipeline
  "src/engines/correlation_gate.py"
  "src/engines/regime_filter.py"
  "src/engines/intermarket_engine.py"
  "src/engines/execution_audit.py"
  "src/engines/trade_ledger.py"

  # Runner orchestration
  "src/runners/local_scanner.py"
  "src/runners/alpha_sweep_runner.py"
  "src/runners/shadow_leaderboard.py"
  "src/runners/forensic_audit.py"
  "src/runners/sovereign_ctl.py"

  # Tools & Cron
  "tools/cookie_guardian/"
  "cron/"

  # Strategy blueprints (replaced by philosophy doc)
  "strategies/strategy_2_mean_reversion.md"
  "strategies/strategy_3_order_flow.md"
  "strategies/future_ideas.md"

  # Internal docs
  "docs/SOVEREIGN_SYSTEM_MANIFESTO.md"
  "docs/BAYESIAN_PIVOT_MANIFESTO.md"
  "docs/BAYESIAN_PIVOT_CASE_STUDY.md"
  "docs/BAYESIAN_PIVOT_TECH_CASE_STUDY.md"
  "docs/BAYESIAN_PIVOT_ALPHA_SPECIFICATION.md"
  "docs/BAYESIAN_PIVOT_ALPHA_SCANNER.md"
  "docs/SOVEREIGN_EXECUTION_PLAN.md"
  "docs/PROP_FIRM_SCALING_PLAN.md"
  "docs/edge_discovery_results.json"
  "docs/ict_oracle_kb.json"
  "agent.md"
  ".agents/"

  # Data & charts (large binary blobs)
  "data/"
  "backtesting/"
)

for f in "${REMOVE_FILES[@]}"; do
  git rm -rf --cached "$f" 2>/dev/null || true
done

# ── Create a .gitignore for the public branch ─────────────────────────────────
cat > .gitignore.public << 'EOF'
# Private implementation files — not published in public mirror
src/engines/smc_scanner_impl.py
src/engines/alpha_sweep_scanner.py
src/engines/champion_challenger_lab.py
src/engines/judas_inducement_engine.py
src/engines/psychology_engine.py
src/engines/execution_shadow_engine.py
src/engines/biometric_engine.py
src/engines/counterfactual_tracker.py
src/engines/inducement_tracker.py
src/engines/multi_account_funnel.py
src/engines/sovereign_engine/
src/sovereign_core/
src/engines/ai_audit_engine.py
src/engines/ai_validator_impl.py
src/engines/correlation_gate.py
src/engines/regime_filter.py
src/engines/intermarket_engine.py
src/engines/execution_audit.py
src/engines/trade_ledger.py
src/runners/local_scanner.py
src/runners/alpha_sweep_runner.py
src/runners/shadow_leaderboard.py
src/runners/forensic_audit.py
src/runners/sovereign_ctl.py
tools/cookie_guardian/
cron/
strategies/future_ideas.md
strategies/strategy_2_*.md
strategies/strategy_3_*.md
docs/SOVEREIGN_SYSTEM_MANIFESTO.md
data/
*.db
*.key
.env*
__pycache__/
EOF

git add .gitignore.public

# Swap in the public redacted README
if [ -f "README.public.md" ]; then
  mv README.public.md README.md
  git add README.md
  git rm --cached README.public.md 2>/dev/null || true
fi

git commit -m "chore: prepare public mirror by stripping proprietary engines and strategies"

echo "🚀 Pushing to $REMOTE/$BRANCH..."
git push "$REMOTE" "$TMP_BRANCH:$BRANCH" --force

echo "🧹 Cleaning up staging branch..."
git checkout main --force
git branch -D "$TMP_BRANCH"

echo "✅ Public mirror updated successfully."
