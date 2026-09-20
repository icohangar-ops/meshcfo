"""Smoke tests for the Multi-Agent CFO Operating System."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cme.cfo_os import (
    BoardBrief,
    CFOOperatingSystem,
    CFOTaskType,
    ForecastBrief,
    InvestmentBrief,
)
from cme.cfo_os.artifacts import BoardOutput, ForecastPack, InvestmentCaseMemo
from cme.chp.models import SessionStatus
from demo import ComplianceAgent, FinanceAgent, StrategyAgent


def _cfo_os() -> CFOOperatingSystem:
    return CFOOperatingSystem(
        agents=[FinanceAgent(), StrategyAgent(), ComplianceAgent()],
        company_name="Acme",
    )


def test_investment_case_runs_three_agents_and_advances_lock():
    cfo = _cfo_os()
    brief = InvestmentBrief(
        title="Fund enterprise tier Q3",
        company="Acme",
        problem="Should we fund a dedicated enterprise tier this quarter?",
        investment_amount_usd=4_000_000,
        expected_payback_months=14,
        minimum_runway_months=12,
        current_runway_months=18,
        expected_upside=["Higher ACV"],
        key_risks=["Adoption lag"],
    )
    report = cfo.run(brief, confirmed_by="finance-lead")

    assert report.brief.task_type == CFOTaskType.INVESTMENT_CASE
    assert isinstance(report.artifact, InvestmentCaseMemo)
    assert {t.agent for t in report.turns} == {"finance", "strategy", "compliance"}
    assert report.case.foundation_score and report.case.foundation_score >= 70
    assert report.case.status == SessionStatus.PROVISIONAL_LOCK
    assert "BEGIN_PAYLOAD" in report.initial_packet
    assert report.audit.entries  # at least one provenance entry


def test_forecast_brief_produces_forecast_pack():
    cfo = _cfo_os()
    brief = ForecastBrief(
        title="FY26 driver-based plan",
        company="Acme",
        problem="Build the FY26 driver-based plan.",
        base_revenue_usd=42_000_000,
        base_opex_usd=15_000_000,
        growth_assumption_pct=0.30,
        churn_assumption_pct=0.08,
    )
    report = cfo.run(brief, confirmed_by="fpna-lead")
    assert isinstance(report.artifact, ForecastPack)
    assert report.case.domain == "forecast"
    rendered = report.artifact.render()
    assert "Driver Registry" in rendered
    assert "Stress Views" in rendered


def test_board_brief_produces_board_output_with_options():
    cfo = _cfo_os()
    brief = BoardBrief(
        title="Q3 board: enterprise expansion",
        company="Acme",
        problem="Approve the enterprise expansion plan.",
        options=["Approve", "Defer", "Reject"],
        recommended_option_index=0,
        open_questions=["Pipeline confidence?"],
        strategic_risks=["Adoption ramp"],
    )
    report = cfo.run(brief, confirmed_by="board-secretary")
    assert isinstance(report.artifact, BoardOutput)
    assert report.case.domain == "board_decision"
    rendered = report.artifact.render()
    assert "(recommended) 1. Approve" in rendered
    assert "Open Questions" in rendered


def test_lock_progression_via_third_party_validation():
    cfo = _cfo_os()
    brief = InvestmentBrief(
        title="Fund enterprise tier Q3",
        company="Acme",
        problem="Should we fund a platform team next quarter?",
        investment_amount_usd=4_000_000,
        expected_payback_months=14,
        minimum_runway_months=12,
        current_runway_months=18,
    )
    report = cfo.run(brief, confirmed_by="finance-lead")
    assert report.case.status == SessionStatus.PROVISIONAL_LOCK

    case = cfo.lock(
        report.case.decision_id,
        validator="fresh_instance",
        item="Investment spec v1",
        rationale="Spec coheres; flip criteria explicit.",
        confirm=True,
    )
    assert case.status == SessionStatus.LOCKED
    assert "Investment spec v1" in case.locked_decisions
    assert len(case.third_party_log) == 1


def test_audit_trail_links_each_agent_to_expansion_steps():
    cfo = _cfo_os()
    brief = InvestmentBrief(
        title="Fund enterprise tier Q3",
        company="Acme",
        problem="Audit smoke test.",
        investment_amount_usd=4_000_000,
        expected_payback_months=14,
        minimum_runway_months=12,
        current_runway_months=20,
    )
    report = cfo.run(brief, confirmed_by="internal-audit")
    agents_in_audit = {e.agent for e in report.audit.entries}
    assert agents_in_audit == {"finance", "strategy", "compliance"}
    # Each agent contributes at least an expansion step + a final recommendation entry.
    for agent in agents_in_audit:
        agent_entries = [e for e in report.audit.entries if e.agent == agent]
        assert len(agent_entries) >= 2
    assert any("recommendation" == e.claim for e in report.audit.entries)
    assert report.audit.foundation_findings  # CHP findings recorded
