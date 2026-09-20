"""Tests for the CHP decision gate (consensus-hardening-protocol 0.1.1).

Mirrors the erp-control-plane CHP suite: R0 refusal before the engine,
finance-floor enforcement below 100, golden-parity mismatch fatality, the
EXPLORING -> PROVISIONAL_LOCK -> LOCKED progression, human-lock enforcement,
and decision-ledger round trip with tamper detection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cme.cfo_os import CFOOperatingSystem, InvestmentBrief
from cme.hardening import (
    ChpDecisionGate,
    ChpGateSettings,
    ChpRejection,
    DecisionLedger,
)
from cme.chp.models import SessionStatus
from demo import ComplianceAgent, FinanceAgent, StrategyAgent

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DEFAULT = REPO_ROOT / "evals" / "golden_qa.json"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolate gate state: decision ledger + golden file under tmp_path."""
    monkeypatch.setenv("MESH_CFO_CHP_DECISIONS_PATH", str(tmp_path / "decisions.jsonl"))
    monkeypatch.setenv("MESH_CFO_CHP_GOLDEN_PATH", str(GOLDEN_DEFAULT))
    return tmp_path


def _cfo() -> CFOOperatingSystem:
    return CFOOperatingSystem(
        agents=[FinanceAgent(), StrategyAgent(), ComplianceAgent()],
        company_name="Acme",
        gate=ChpDecisionGate(ChpGateSettings.from_env()),
    )


def _golden_brief() -> InvestmentBrief:
    """An investment brief matching the checked-in golden QA baseline."""
    return InvestmentBrief(
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


def test_r0_refusal_blocks_session_before_agents_run(env):
    """An unscoped brief is refused at R0 — no agent runs, nothing is recorded."""
    brief = InvestmentBrief(
        title="Vague ask",
        company="Acme",
        problem="",  # no scope → R0 cannot establish Scoped
        investment_amount_usd=4_000_000,
        expected_payback_months=14,
    )
    cfo = _cfo()
    with pytest.raises(ChpRejection, match="R0"):
        cfo.run(brief, confirmed_by="finance-lead")

    # The refusal is audited, but nothing reached the decision ledger.
    assert any(r["event"] == "chp_rejected" for r in cfo.ledger.read_all())
    ledger = DecisionLedger(ChpGateSettings.from_env().decisions_path)
    assert ledger.list() == []


def test_finance_floor_refusal_below_100(env):
    """An investment case without golden parity scores 70 < finance floor 100."""
    brief = InvestmentBrief(
        title="Fund platform team",  # not in the golden QA set
        company="Acme",
        problem="Should we fund a platform team next quarter?",
        investment_amount_usd=1_500_000,
        expected_payback_months=12,
        minimum_runway_months=12,
        current_runway_months=18,
    )
    cfo = _cfo()
    with pytest.raises(ChpRejection) as exc:
        cfo.run(brief, confirmed_by="finance-lead")
    assert "capital_allocation" in str(exc.value)
    assert "100" in str(exc.value)


def test_parity_mismatch_is_fatal_even_with_confirmer(env, tmp_path, monkeypatch):
    """A headline number contradicting the golden baseline is refused —
    confirmed_by cannot paper over a parity mismatch."""
    bad_golden = tmp_path / "golden_bad.json"
    bad_golden.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "demo_investment_enterprise_tier",
                        "task_type": "investment_case",
                        "title": "Fund enterprise tier Q3",
                        "metric": "investment_amount_usd",
                        "unit": "usd",
                        "expected": 5_000_000,  # contradicts the brief's $4.0M
                        "tolerance": 0.005,
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("MESH_CFO_CHP_GOLDEN_PATH", str(bad_golden))
    cfo = _cfo()
    with pytest.raises(ChpRejection, match="MISMATCH"):
        cfo.run(_golden_brief(), confirmed_by="finance-lead")


def test_session_progresses_exploring_to_provisional_lock(env, monkeypatch):
    """With the human-lock flag off, an unconfirmed session holds at
    PROVISIONAL_LOCK and locks only via third-party validation."""
    monkeypatch.setenv("MESH_CFO_CHP_REQUIRE_HUMAN_LOCK", "0")
    cfo = _cfo()
    report = cfo.run(_golden_brief(), confirmed_by=None)

    assert report.hardening["r0_verdict"] == "PASS"
    assert report.hardening["foundation_score"] == 100
    assert report.hardening["parity"]["within_tolerance"] is True
    assert report.hardening["confirmed_by"] is None
    assert report.case.status == SessionStatus.PROVISIONAL_LOCK

    case = cfo.lock(
        report.case.decision_id,
        validator="cfo-chair",
        item="Investment spec v1",
        rationale="Spec coheres; flip criteria explicit.",
        confirm=True,
    )
    assert case.status == SessionStatus.LOCKED


def test_human_lock_enforcement(env):
    """With the flag on (default), a passing session without a named confirmer
    is refused outright — the claim never reaches even provisional lock."""
    cfo = _cfo()
    with pytest.raises(ChpRejection, match="human lock"):
        cfo.run(_golden_brief(), confirmed_by=None)


def test_decision_ledger_roundtrip_and_tamper_detection(env):
    """A sealed record reads back with valid integrity; editing the stored body
    without recomputing the digest flips integrity_valid to False while the
    structure-only CHP envelope check still passes."""
    cfo = _cfo()
    report = cfo.run(_golden_brief(), confirmed_by="finance-lead")

    path = ChpGateSettings.from_env().decisions_path
    assert path.exists()

    ledger = DecisionLedger(path)
    records = ledger.list()
    assert len(records) == 1
    record = records[0]
    assert record["decision_id"] == report.case.decision_id
    assert record["session_status"] == "LOCKED"
    assert record["envelope_valid"] is True
    assert record["integrity_valid"] is True
    assert record["body_sha256"] == report.hardening["ledger_body_sha256"]

    fetched = ledger.get(report.case.decision_id)
    assert fetched is not None and fetched["integrity_valid"] is True

    # Tamper: rewrite the stored body (inflated score) without recomputing
    # the digest. The CHP envelope stays structurally valid — it never
    # promised content integrity — but our SHA-256 check must catch it.
    lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[-1])
    body = json.loads(raw["body"])
    body["foundation_score"] = 100 if body["foundation_score"] != 100 else 99
    raw["body"] = json.dumps(body, sort_keys=True, ensure_ascii=False)
    lines[-1] = json.dumps(raw, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    tampered = DecisionLedger(path).list()[0]
    assert tampered["envelope_valid"] is True
    assert tampered["integrity_valid"] is False


def test_decision_ledger_handles_missing_and_empty_files(env, tmp_path, monkeypatch):
    """An absent or empty ledger reads as empty — no tracebacks from parsers."""
    monkeypatch.setenv("MESH_CFO_CHP_DECISIONS_PATH", str(tmp_path / "absent.jsonl"))
    ledger = DecisionLedger(ChpGateSettings.from_env().decisions_path)
    assert ledger.list() == []
    assert ledger.get("any-id") is None

    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    monkeypatch.setenv("MESH_CFO_CHP_DECISIONS_PATH", str(empty))
    assert DecisionLedger(ChpGateSettings.from_env().decisions_path).list() == []


def test_gate_loop_and_ledger_integration(env):
    """Full loop: gated session -> hardening summary on the report -> record
    sealed in the append-only ledger -> post-run lock appends a lock event."""
    cfo = _cfo()
    report = cfo.run(_golden_brief(), confirmed_by="finance-lead")

    hardening = report.hardening
    assert hardening["decision_id"] == report.case.decision_id
    assert hardening["r0_verdict"] == "PASS"
    assert hardening["foundation_verdict"] == "PASS"
    assert hardening["domain"] == "capital_allocation"
    assert hardening["session_status"] == "LOCKED"
    assert hardening["parity"]["case_id"] == "demo_investment_enterprise_tier"

    ledger = DecisionLedger(ChpGateSettings.from_env().decisions_path)
    record = ledger.get(report.case.decision_id)
    assert record is not None
    assert record["envelope_valid"] and record["integrity_valid"]
    body = json.loads(record["body"])
    assert body["r0_verdict"] == "PASS"
    assert body["foundation_score"] == 100
    assert "Finance" in body["artifact_markdown"] or "Investment Inputs" in body["artifact_markdown"]


def test_board_session_gates_at_finance_floor_with_options_parity(env):
    """Board output maps to board_decision (finance floor 100); the option
    count is the parity metric."""
    from cme.cfo_os import BoardBrief

    cfo = _cfo()
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
        open_questions=["Is pipeline conversion confidence supported by recent cohorts?"],
        strategic_risks=["Adoption ramp slope"],
    )
    report = cfo.run(brief, confirmed_by="board-secretary")
    assert report.hardening["domain"] == "board_decision"
    assert report.hardening["foundation_score"] == 100
    assert report.hardening["parity"]["metric"] == "board_options_count"
    assert report.hardening["parity"]["within_tolerance"] is True
