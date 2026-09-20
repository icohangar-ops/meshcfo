"""End-to-end demo of the Multi-Agent CFO Operating System.

Runs the same three Mesh agents (Finance, Strategy, Compliance) under CHP
hardening across all three CFO task types, and prints each session report.

Every session runs through the CHP decision gate (``cme.hardening``): R0
before the orchestration, deterministic adversary scoring with golden parity,
and a human lock. The demo names ``demo-operator`` as the confirmer so it
stays runnable out of the box; in production the confirmer is a named human
(and MESH_CFO_CHP_REQUIRE_HUMAN_LOCK defaults on to demand one).
"""
from __future__ import annotations

from cme.cfo_os import (
    BoardBrief,
    CFOOperatingSystem,
    ForecastBrief,
    InvestmentBrief,
)
from demo import ComplianceAgent, FinanceAgent, StrategyAgent

DEMO_CONFIRMER = "demo-operator"


def run_investment_demo(cfo: CFOOperatingSystem) -> str:
    brief = InvestmentBrief(
        title="Fund enterprise tier Q3",
        company="Acme",
        problem=(
            "Should we fund a dedicated enterprise tier this quarter, or extend "
            "the SMB product to cover enterprise use cases?"
        ),
        investment_amount_usd=4_000_000,
        expected_payback_months=14,
        minimum_runway_months=12,
        current_runway_months=18,
        expected_upside=["Higher ACV", "Lower strategic-account churn"],
        key_risks=["Adoption lag", "Implementation complexity"],
        strategic_priorities=["Expand enterprise ARR", "Preserve capital discipline"],
    )
    return cfo.run(brief, confirmed_by=DEMO_CONFIRMER).render()


def run_forecast_demo(cfo: CFOOperatingSystem) -> str:
    brief = ForecastBrief(
        title="FY26 driver-based plan",
        company="Acme",
        problem="Build the FY26 driver-based operating plan with stress views.",
        base_revenue_usd=42_000_000,
        base_opex_usd=33_000_000,
        growth_assumption_pct=0.28,
        churn_assumption_pct=0.09,
        minimum_runway_months=12,
        current_runway_months=20,
        strategic_priorities=["Net dollar retention >= 115%"],
    )
    return cfo.run(brief, confirmed_by=DEMO_CONFIRMER).render()


def run_board_demo(cfo: CFOOperatingSystem) -> str:
    brief = BoardBrief(
        title="Q3 board: enterprise expansion",
        company="Acme",
        problem="Approve the FY26 enterprise expansion plan with phased capital release.",
        options=[
            "Approve phased capital release with milestone gates",
            "Defer one quarter pending pipeline confirmation",
            "Reject and reinvest in SMB retention",
        ],
        recommended_option_index=0,
        open_questions=[
            "Is enterprise pipeline conversion confidence supported by recent cohorts?",
            "Does compliance posture cover SOC2 + data residency at scale?",
        ],
        strategic_risks=["Adoption ramp slope", "Compliance scope creep"],
        strategic_priorities=["Expand enterprise ARR", "Preserve capital discipline"],
    )
    return cfo.run(brief, confirmed_by=DEMO_CONFIRMER).render()


def main() -> int:
    cfo = CFOOperatingSystem(
        agents=[FinanceAgent(), StrategyAgent(), ComplianceAgent()],
        company_name="Acme",
    )
    print(run_investment_demo(cfo))
    print("\n\n=== next task ===\n\n")
    print(run_forecast_demo(cfo))
    print("\n\n=== next task ===\n\n")
    print(run_board_demo(cfo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
