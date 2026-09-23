"""
Emergency Portfolio Kill Switch
===============================
Instant panic-button script for portfolio-wide risk mitigation.
Cancels all pending orders and closes all active open positions across
ALL 8 TradeLocker account mandates simultaneously.
"""

import sys
import os
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.clients.tl_client import TradeLockerClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("KillSwitch")

def execute_emergency_kill_switch():
    print("\n===========================================================================")
    print(" 🚨 EMERGENCY PORTFOLIO KILL SWITCH TRIGGERED")
    print("===========================================================================\n")

    client = TradeLockerClient()
    print(f" • Loaded {len(client.helpers)} TradeLocker Account Helpers.\n")
    print(" • Closing all open positions across fleet with 2.0s adaptive pacing...")

    total_closed = client.close_all_fleet_positions()

    print("\n===========================================================================")
    print(f" 🏁 KILL SWITCH EXECUTION COMPLETE: Successfully closed {total_closed} position(s).")
    print("===========================================================================\n")

if __name__ == "__main__":
    execute_emergency_kill_switch()
