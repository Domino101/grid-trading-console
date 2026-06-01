#!/usr/bin/env python3
"""
Hermes Grid Integration Demo Script

This script showcases how the Hermes Agent uses the `HermesGridClient` and `HermesGridController`
to query system health, retrieve grid summaries, make AI-driven decisions, and manage
grid lifetimes programmatically.

To run this demo with a live FastAPI backend:
    1. Make sure your .env has GRID_API_TOKEN configured.
    2. Start the FastAPI server on port 8001 (e.g. using `bash start_web_ui.sh`).
    3. Run this script:
       python3 tools/hermes_grid_demo.py
"""

import os
import sys
import asyncio
import logging
from typing import List

# Ensure project root is in path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tools.hermes_grid_client import (
    HermesGridClient, 
    HermesGridController, 
    HermesDecision, 
    logger as client_logger
)

# Set demo logs to print nicely
logger = logging.getLogger("HermesGridDemo")
handler = logging.StreamHandler()
formatter = logging.Formatter('\033[94m%(asctime)s - %(name)s - %(levelname)s - %(message)s\033[0m')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class RuleBasedHermesController(HermesGridController):
    """A sample implementation of the Hermes Grid Controller with rule-based decision logic"""

    def __init__(self, grid_api_url: str, api_token: str, mock_target_symbol: str = "SOL/USDC:USDC"):
        super().__init__(grid_api_url, api_token)
        self.mock_target_symbol = mock_target_symbol

    async def ai_analyze(self, summary: dict) -> List[HermesDecision]:
        """Custom implementation of AI analysis
        
        Rules:
        1. If total active grids < 2, create a new grid for SOL/USDC.
        2. If any grid is running with negative total PnL <-100.0, stop it.
        """
        logger.info("🤖 [AI Analyzer] Analyzing market metrics and running grids...")
        decisions = []

        total_grids = summary.get("total_grids", 0)
        active_grids = summary.get("active_grids", 0)
        grids_list = summary.get("grids", [])

        logger.info(f"🤖 [AI Analyzer] Current Stats: Total Grids = {total_grids}, Active Grids = {active_grids}")

        # Rule 1: Under-allocated -> Create a new neutral grid
        is_mock_symbol_running = any(g.get("symbol") == self.mock_target_symbol and g.get("status") == "running" for g in grids_list)
        if active_grids < 2 and not is_mock_symbol_running:
            logger.info(f"🤖 [AI Analyzer] Rule 1 Triggered: Active grids ({active_grids}) < 2. Generating neutral grid for {self.mock_target_symbol}")
            decisions.append(
                HermesDecision(
                    action="create",
                    params={
                        "symbol": self.mock_target_symbol,
                        "lower": 120.0,
                        "upper": 180.0,
                        "interval": 0.5,
                        "amount": 0.1
                    }
                )
            )

        # Rule 2: Risk Management -> Stop underperforming grids
        for grid in grids_list:
            grid_id = grid.get("grid_id")
            pnl = grid.get("total_pnl", 0.0)
            status = grid.get("status")
            if status == "running" and pnl < -100.0:
                logger.warning(f"🤖 [AI Analyzer] Rule 2 Triggered: Grid {grid_id} has severe loss ({pnl} USDC). Generating stop action.")
                decisions.append(
                    HermesDecision(
                        action="stop",
                        grid_id=grid_id
                    )
                )

        if not decisions:
            logger.info("🤖 [AI Analyzer] All health and performance metrics are optimal. No actions required.")
            
        return decisions


async def run_demo():
    print("=" * 70)
    print("      🤖 HERMES GRID TRADING INTEGRATION DEMO - PHASE 4 🤖")
    print("=" * 70)

    # Initialize HermesGridClient with defaults
    base_url = "http://localhost:8001"
    token = os.environ.get("GRID_API_TOKEN", "xxx")

    logger.info(f"Configuring client base URL: {base_url}")
    logger.info(f"API Token configured: {'[LOADED FROM ENV]' if token != 'xxx' else '[DEFAULT xxx - MIGHT FAIL UNLESS PRE-CONFIGURED]'}")
    
    client = HermesGridClient(base_url, token)

    # Pre-check: Check if FastAPI port 8001 is listening
    logger.info("Testing connection to FastAPI server...")
    try:
        is_ready = await client.is_system_ready()
        logger.info(f"✅ Connection successful! System Health Ready Status: {is_ready}")
    except Exception as e:
        logger.error("❌ Connection failed! Please make sure your FastAPI server is running on port 8001.")
        logger.error("You can start it by running: bash start_web_ui.sh")
        print("\n" + "!" * 70)
        print("    RUNNING IN OFFLINE SIMULATION MODE")
        print("!" * 70 + "\n")
        
        # Run a simulated dry-run demonstration
        await run_simulated_demo()
        return

    # Connection succeeded: Run full integration steps
    print("\n--- Step 1: Health Check ---")
    is_ready = await client.is_system_ready()
    logger.info(f"System Ready to accept new grids: {is_ready}")

    print("\n--- Step 2: Query Global P&L Summary ---")
    summary = await client.get_pnl_summary()
    logger.info(f"Global Summary Response: {summary}")

    print("\n--- Step 3: Instantiate Hermes Grid Controller & Run AI Decisions ---")
    controller = RuleBasedHermesController(base_url, token)
    
    # Run a single analyzer run manually to inspect decisions
    decisions = await controller.ai_analyze(summary)
    print(f"Generated decisions: {decisions}")

    for decision in decisions:
        print(f"\nExecuting programmatically: {decision}")
        if decision.action == "create":
            res = await client.create_neutral_grid(**decision.params)
            logger.info(f"Grid Creation Result: {res}")
            grid_id = res.get("grid_id")
            
            # Immediately query real-time stats of newly created grid
            await asyncio.sleep(1)
            print(f"\n--- Querying Stats for Newly Created Grid: {grid_id} ---")
            try:
                stats = await client.get_pnl_summary()
                logger.info(f"Updated Summary containing new grid: {stats}")
            except Exception as e:
                logger.error(f"Failed to query stats: {e}")
                
        elif decision.action == "stop":
            res = await client.stop_grid(decision.grid_id)
            logger.info(f"Grid Stop Result: {res}")

    print("\n" + "=" * 70)
    print("      🎉 LIVE INTEGRATION DEMO COMPLETED SUCCESSFULLY 🎉")
    print("=" * 70)


async def run_simulated_demo():
    """Outputs a simulated log flow exactly mimicking a running system for validation"""
    print("\n[SIMULATION] Starting Simulated monitor loop sequence:")
    print("1. [HEALTH_CHECK] Sending GET /api/v1/system/health...")
    await asyncio.sleep(0.5)
    print("   ↳ Status: 200 OK | can_accept_new_grid: True | circuit_breaker: Healthy")
    
    print("\n2. [SUMMARY] Sending GET /api/v1/grids/summary...")
    await asyncio.sleep(0.5)
    print("   ↳ Status: 200 OK | total_grids: 1 | active_grids: 1")
    print('     Realized P&L: 14.85 USDC | Total P&L: 12.30 USDC')

    print("\n3. [AI_ANALYZER] Rules running in custom HermesGridController...")
    await asyncio.sleep(0.8)
    print("   🤖 [AI Analyzer] Active grids (1) < 2. Generating neutral grid for SOL/USDC:USDC")
    print("   🤖 Decisions Generated: [HermesDecision(action=create, params={'symbol': 'SOL/USDC:USDC', 'lower': 120.0, 'upper': 180.0, ...})]")

    print("\n4. [EXECUTE] Sending POST /api/v1/grids with AI-generated parameters...")
    await asyncio.sleep(0.6)
    print("   ↳ Status: 201 Created | grid_id: binance-neutral-perp-sol-usdc-120-180")
    print("     Message: 網格 binance-neutral-perp-sol-usdc-120-180 已成功建立，引擎將在 ~5 秒內自動啟動")

    print("\n5. [COMPLETED] Simulated monitor loop completed. Next loop scheduled in 30 minutes.")
    print("\n" + "=" * 70)
    print("      🎉 SIMULATION INTEGRATION DEMO COMPLETED 🎉")
    print("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        print("\nDemo interrupted by user.")
