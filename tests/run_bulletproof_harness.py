#!/usr/bin/env python3
"""
================================================================================
🏛️ BAYESIAN PIVOT // SOVEREIGN MASTER INVARIANT & CRUCIBLE HARNESS
================================================================================
Master Invariant Verification & Regression Prevention Test Suite.
Verifies Broker Safety (AGENTS.md), Quantitative Physics, Prop Compliance, and
Live WebSocket Orderflow Feeds.
"""

import sys
import os
import time
import unittest

# Ensure root directory is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ANSI Color Tokens
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_tier(suite_module_name: str, tier_title: str) -> dict:
    """Executes a single test suite tier and returns result metrics."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(suite_module_name)
    
    start_time = time.time()
    # Run test suite with quiet runner to capture results cleanly
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    elapsed = time.time() - start_time
    
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - (failures + errors)
    is_clean = (failures == 0 and errors == 0)

    return {
        "title": tier_title,
        "module": suite_module_name,
        "total": total,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "elapsed": elapsed,
        "is_clean": is_clean,
        "failure_details": result.failures + result.errors
    }


def main():
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║     🏛️  SOVEREIGN MASTER INVARIANT & QUANT EXECUTION CRUCIBLE HARNESS         ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════════════════════════╝{RESET}\n")

    test_tiers = [
        ("tests.test_broker_invariants", "TIER 1: BROKER & EXECUTION INVARIANTS (AGENTS.md)"),
        ("tests.test_quant_invariants", "TIER 2: QUANTITATIVE PHYSICS & HURST INVARIANTS"),
        ("tests.test_prop_compliance_invariants", "TIER 3: PROP COMPLIANCE & RETRAINING INVARIANTS"),
        ("tests.test_live_orderflow_feed", "TIER 4: LIVE ORDERFLOW & ICEBERG ABSORPTION FEED"),
        ("tests.test_pipeline_e2e_invariants", "TIER 5: SIGNAL PIPELINE & GRADUATED ARCHETYPE INVARIANTS"),
        ("tests.test_adversarial_boundary_invariants", "TIER 6: ADVERSARIAL NEGATIVE BOUNDARY & STATE INVARIANTS"),
        ("tests.test_autonomous_adversarial_quality_loop", "TIER 7: AUTONOMOUS QUANT EXECUTION CRUCIBLE (AQEC FUZZ)"),
        ("tests.test_crucible_mutations", "TIER 8: 3D MUTATION META-TESTING (TESTING THE TEST)"),
    ]

    tier_results = []
    overall_start = time.time()

    for module_name, tier_title in test_tiers:
        print(f"{BOLD}▶ Running {tier_title}...{RESET}")
        res = run_tier(module_name, tier_title)
        tier_results.append(res)
        
        status_tag = f"{GREEN}✔ PASSED ({res['passed']}/{res['total']}){RESET}" if res['is_clean'] else f"{RED}✘ FAILED ({res['failures'] + res['errors']} issues){RESET}"
        print(f"  └─ Status: {status_tag} in {res['elapsed']:.3f}s\n")

    overall_elapsed = time.time() - overall_start
    total_tests = sum(r["total"] for r in tier_results)
    total_passed = sum(r["passed"] for r in tier_results)
    total_failed = sum(r["failures"] + r["errors"] for r in tier_results)
    all_clean = all(r["is_clean"] for r in tier_results)

    # Print Institutional Summary Card
    print(f"{BOLD}{CYAN}──────────────────────────────────────────────────────────────────────────────────{RESET}")
    print(f"{BOLD}📊 BULLETPROOF HARNESS AUDIT SCORECARD:{RESET}")
    print(f"{BOLD}{CYAN}──────────────────────────────────────────────────────────────────────────────────{RESET}")
    
    for r in tier_results:
        icon = f"{GREEN}✅{RESET}" if r["is_clean"] else f"{RED}❌{RESET}"
        print(f" {icon} {r['title']:<55} [{r['passed']}/{r['total']} passed] ({r['elapsed']:.3f}s)")

    print(f"{BOLD}{CYAN}──────────────────────────────────────────────────────────────────────────────────{RESET}")
    
    if all_clean:
        print(f"\n{BOLD}{GREEN}🏆 ZERO REGRESSIONS DETECTED — ALL {total_tests} INVARIANT CHECKS PASSED PERFECTLY ({overall_elapsed:.2f}s).{RESET}")
        print(f"{GREEN}• Zero-Naked Orders Invariant: VERIFIED{RESET}")
        print(f"{GREEN}• In-Place PATCH Brackets: VERIFIED{RESET}")
        print(f"{GREEN}• DELETE Position Termination: VERIFIED{RESET}")
        print(f"{GREEN}• Hurst Chaos Gate Rejection: VERIFIED{RESET}")
        print(f"{GREEN}• High-Impact News Lockout: VERIFIED{RESET}")
        print(f"{GREEN}• Live Tick CVD Iceberg Detection: VERIFIED{RESET}\n")
        sys.exit(0)
    else:
        print(f"\n{BOLD}{RED}🚨 CRITICAL REGRESSION ALERT — {total_failed}/{total_tests} CHECKS FAILED!{RESET}")
        for r in tier_results:
            if not r["is_clean"]:
                print(f"\n{RED}--- Failure in {r['title']} ---{RESET}")
                for test_case, trace in r["failure_details"]:
                    print(f"{YELLOW}Test: {test_case}{RESET}\n{trace}")
        sys.exit(1)


if __name__ == "__main__":
    main()
