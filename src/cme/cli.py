"""Command-line entry point for the Multi-Agent CFO Operating System."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

from cme.bridge import EntryPoint
from cme.cfo_os import (
    BoardBrief,
    CFOOperatingSystem,
    CFOTaskType,
    ForecastBrief,
    InvestmentBrief,
)
from cme.chp import CHPOrchestrator, DecisionRegistry, Phase, ThirdPartyValidation, ValidationResult
from cme.context import ContextEngine, Entity, Task
from cme.finance import CapitalAllocationInput, build_capital_allocation_case
from cme.orchestrator import EnterpriseOrchestrator


def _maybe_init_governance() -> None:
    """Initialize EGIS governance if the SDK is available and enabled."""
    enabled = os.getenv("EGISAI_ENABLED", "true").lower() == "true"
    if not enabled:
        return
    try:
        from cme.governance import init_governance  # noqa: WPS433

        init_governance()
    except Exception:
        pass  # governance not available — proceed without it


def _registry_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "registry", ".chp_registry.json"))


def _default_agents() -> List:
    from demo import FinanceAgent, StrategyAgent, ComplianceAgent  # noqa: WPS433

    return [FinanceAgent(), StrategyAgent(), ComplianceAgent()]


def _seed_org_context(ctx: ContextEngine) -> None:
    ctx.upsert_entity(Entity(id="org", type="org", attributes={"name": "Aperture Corp", "fiscal_year": "2026"}))
    ctx.upsert_entity(Entity(id="finance_ops", type="team", attributes={"name": "Finance Ops", "lead": "M. Osei"}))
    ctx.upsert_entity(Entity(id="gtm", type="team", attributes={"name": "Go-To-Market", "lead": "A. Rivera"}))
    ctx.upsert_entity(
        Entity(
            id="metric_ndr",
            type="metric",
            attributes={"name": "Net Dollar Retention", "current": 1.08, "target": 1.15},
        )
    )
    ctx.upsert_entity(
        Entity(id="policy_reserve", type="policy", attributes={"name": "Regulatory reserve ratio", "value": 0.12})
    )
    ctx.add_task(Task(id="T1", goal="Align on FY26 growth bet", status="in_progress", owner="exec"))


def _cmd_demo(args: argparse.Namespace) -> int:
    ctx = ContextEngine()
    _seed_org_context(ctx)
    orchestrator = EnterpriseOrchestrator(agents=_default_agents(), context=ctx)

    problem = args.problem or (
        "Should we invest $4M in building a dedicated enterprise tier next quarter, "
        "or extend the existing SMB product to cover enterprise use cases?"
    )
    report = orchestrator.orchestrate(
        problem,
        entry_point=EntryPoint(args.entry_point),
        workflow_title=args.title,
    )

    if args.json:
        out = {
            "problem": report.problem,
            "duration_ms": report.duration_ms,
            "agents": [
                {
                    "name": t.agent,
                    "recommendation": t.trace.recommendation,
                    "confidence": t.trace.confidence.value,
                    "playbook_deltas": t.deltas_applied,
                }
                for t in report.turns
            ],
            "workflow": report.workflow.to_dict(),
            "statement_completeness": report.workflow.statement.completeness_report(),
        }
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write(report.render() + "\n")

    if args.out:
        Path(args.out).write_text(report.render())
        sys.stderr.write(f"\n[wrote markdown report to {args.out}]\n")
    return 0


def _cmd_playbook(args: argparse.Namespace) -> int:
    from demo import FinanceAgent, StrategyAgent, ComplianceAgent

    mapping = {
        "finance": FinanceAgent,
        "strategy": StrategyAgent,
        "compliance": ComplianceAgent,
    }
    cls = mapping.get(args.agent)
    if not cls:
        sys.stderr.write(f"Unknown agent: {args.agent}\n")
        return 2
    agent = cls()
    if args.json:
        sys.stdout.write(agent.playbook.to_json() + "\n")
    else:
        sys.stdout.write(agent.playbook.render_for_generator() + "\n")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    ctx = ContextEngine()
    _seed_org_context(ctx)
    sys.stdout.write(ctx.dump_json() + "\n")
    return 0


def _cmd_chp_start(args: argparse.Namespace) -> int:
    registry = DecisionRegistry.load(_registry_path(args))
    orch = CHPOrchestrator(registry=registry)
    case, disclosure, attack = build_capital_allocation_case(
        CapitalAllocationInput(
            title=args.title,
            company=args.company,
            proposal_summary=args.problem,
            investment_amount_usd=args.amount,
            expected_payback_months=args.payback_months,
            minimum_runway_months=args.min_runway,
            current_runway_months=args.current_runway,
            strategic_priorities=args.priority,
            key_risks=args.risk,
            expected_upside=args.upside,
            origin_model=args.origin_model,
            partner_model=args.partner_model,
            partner_system=args.partner_system,
        )
    )
    report = orch.run_initial_session(
        case=case,
        foundation_disclosure=disclosure,
        foundation_attack=attack,
    )
    if args.json:
        out = {
            "case": report.case.to_dict(),
            "r0_verdict": report.r0_verdict.value,
            "foundation_verdict": report.foundation_verdict.value,
            "initial_packet": report.initial_packet,
        }
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write(report.render() + "\n")
    registry.save(_registry_path(args))
    sys.stderr.write(f"[saved CHP registry to {_registry_path(args)}]\n")
    return 0


def _cmd_chp_receive(args: argparse.Namespace) -> int:
    registry = DecisionRegistry.load(_registry_path(args))
    orch = CHPOrchestrator(registry=registry)
    packet = Path(args.packet_file).read_text()
    case = orch.receive_partner_packet(
        decision_id=args.decision_id,
        partner_packet=packet,
        phase=Phase(args.phase),
        round_number=args.round,
        payload_echo=args.payload_echo,
        snapshot_status=args.status,
    )
    registry.save(_registry_path(args))
    if args.json:
        sys.stdout.write(json.dumps(case.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(
            f"Received packet for {case.decision_id}\n"
            f"status={case.status.value}\n"
            f"phase={case.current_phase.value}\n"
            f"round={case.current_round}\n"
        )
    return 0


def _cmd_chp_validate(args: argparse.Namespace) -> int:
    registry = DecisionRegistry.load(_registry_path(args))
    orch = CHPOrchestrator(registry=registry)
    validation = ThirdPartyValidation(
        validator=args.validator,
        item=args.item,
        challenge=args.challenge,
        result=ValidationResult(args.result),
        rationale=args.rationale,
    )
    case = orch.apply_validation(args.decision_id, validation)
    registry.save(_registry_path(args))
    if args.json:
        sys.stdout.write(json.dumps(case.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(
            f"Validated {case.decision_id}\n"
            f"status={case.status.value}\n"
            f"locked={', '.join(case.locked_decisions) or 'NONE'}\n"
        )
    return 0


def _cmd_cfo_os(args: argparse.Namespace) -> int:
    registry = DecisionRegistry.load(_registry_path(args))
    ctx = ContextEngine()
    _seed_org_context(ctx)
    cfo = CFOOperatingSystem(
        agents=_default_agents(),
        registry=registry,
        context=ctx,
        company_name=args.company,
    )

    task = CFOTaskType(args.task)
    common = dict(
        title=args.title,
        company=args.company,
        problem=args.problem,
        owner=args.owner,
        origin_model=args.origin_model,
        partner_model=args.partner_model,
        partner_system=args.partner_system,
        strategic_priorities=args.priority,
        constraints=args.constraint,
    )
    if task == CFOTaskType.FORECAST:
        brief = ForecastBrief(
            **common,
            base_revenue_usd=args.base_revenue,
            base_opex_usd=args.base_opex,
            growth_assumption_pct=args.growth_pct,
            churn_assumption_pct=args.churn_pct,
            minimum_runway_months=args.min_runway,
            current_runway_months=args.current_runway,
        )
    elif task == CFOTaskType.INVESTMENT_CASE:
        brief = InvestmentBrief(
            **common,
            investment_amount_usd=args.amount or 0.0,
            expected_payback_months=args.payback_months or 18,
            minimum_runway_months=args.min_runway,
            current_runway_months=args.current_runway,
            expected_upside=args.upside,
            key_risks=args.risk,
        )
    else:
        brief = BoardBrief(
            **common,
            options=args.option or [],
            recommended_option_index=args.recommended_index,
            open_questions=args.open_question,
            prior_board_decisions=args.prior_decision,
            strategic_risks=args.risk,
        )

    report = cfo.run(brief)
    registry.save(_registry_path(args))

    if args.json:
        out = {
            "task": task.value,
            "decision_id": report.case.decision_id,
            "lock_state": report.case.status.value,
            "foundation_score": report.case.foundation_score,
            "r0_verdict": report.r0_verdict.value,
            "foundation_verdict": report.foundation_verdict.value,
            "artifact_markdown": report.artifact.render(),
            "audit_entries": [
                {
                    "agent": e.agent,
                    "claim": e.claim,
                    "expansion_label": e.expansion_label,
                    "grounding_source": e.grounding_source,
                    "grounding_confidence": e.grounding_confidence,
                    "risk_flag": e.risk_flag,
                }
                for e in report.audit.entries
            ],
            "foundation_findings": report.audit.foundation_findings,
            "case": report.case.to_dict(),
            "initial_packet": report.initial_packet,
        }
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write(report.render() + "\n")

    if args.out_md:
        Path(args.out_md).write_text(report.render())
        sys.stderr.write(f"[wrote markdown report to {args.out_md}]\n")
    sys.stderr.write(f"[saved CHP registry to {_registry_path(args)}]\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cfo-os",
        description="Multi-Agent CFO Operating System — Mesh + CHP for forecast / investment / board decisions.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="Run the base mesh orchestration on a problem.")
    d.add_argument("problem", nargs="?")
    d.add_argument("--entry-point", choices=[e.value for e in EntryPoint], default=EntryPoint.PROBLEM.value)
    d.add_argument("--title", default=None)
    d.add_argument("--json", action="store_true")
    d.add_argument("--out", default=None)
    d.set_defaults(func=_cmd_demo)

    pb = sub.add_parser("playbook", help="Show a seeded agent playbook.")
    pb.add_argument("agent", choices=["finance", "strategy", "compliance"])
    pb.add_argument("--json", action="store_true")
    pb.set_defaults(func=_cmd_playbook)

    c = sub.add_parser("context", help="Dump the seeded organizational context.")
    c.set_defaults(func=_cmd_context)

    chp = sub.add_parser("chp-start", help="Start a CHP capital allocation session.")
    chp.add_argument("--registry", default=".chp_registry.json")
    chp.add_argument("--title", required=True)
    chp.add_argument("--company", default="Unknown Co")
    chp.add_argument("--problem", required=True)
    chp.add_argument("--amount", type=float, required=True)
    chp.add_argument("--payback-months", type=int, required=True)
    chp.add_argument("--min-runway", type=int, default=12)
    chp.add_argument("--current-runway", type=int, required=True)
    chp.add_argument("--priority", action="append", default=[])
    chp.add_argument("--risk", action="append", default=[])
    chp.add_argument("--upside", action="append", default=[])
    chp.add_argument("--origin-model", default="GPT-5.4")
    chp.add_argument("--partner-model", default="GPT-5-equivalent")
    chp.add_argument("--partner-system", default="Partner")
    chp.add_argument("--json", action="store_true")
    chp.set_defaults(func=_cmd_chp_start)

    chp_receive = sub.add_parser("chp-receive", help="Attach a partner packet to an existing CHP decision.")
    chp_receive.add_argument("--registry", default=".chp_registry.json")
    chp_receive.add_argument("--decision-id", required=True)
    chp_receive.add_argument("--packet-file", required=True)
    chp_receive.add_argument("--phase", type=int, choices=[0, 1, 2], required=True)
    chp_receive.add_argument("--round", type=int, required=True)
    chp_receive.add_argument(
        "--status",
        choices=["EXPLORING", "PROVISIONAL", "PROVISIONAL_LOCK", "LOCKED", "UNRESOLVED"],
        default="EXPLORING",
    )
    chp_receive.add_argument("--payload-echo", default="")
    chp_receive.add_argument("--json", action="store_true")
    chp_receive.set_defaults(func=_cmd_chp_receive)

    chp_validate = sub.add_parser("chp-validate", help="Apply third-party validation to a CHP decision.")
    chp_validate.add_argument("--registry", default=".chp_registry.json")
    chp_validate.add_argument("--decision-id", required=True)
    chp_validate.add_argument("--validator", required=True)
    chp_validate.add_argument("--item", required=True)
    chp_validate.add_argument("--challenge", required=True)
    chp_validate.add_argument("--result", choices=["CONFIRM", "REJECT"], required=True)
    chp_validate.add_argument("--rationale", required=True)
    chp_validate.add_argument("--json", action="store_true")
    chp_validate.set_defaults(func=_cmd_chp_validate)

    cfo = sub.add_parser(
        "cfo-os",
        help="Run the Multi-Agent CFO Operating System on a forecast/investment/board task.",
    )
    cfo.add_argument("--registry", default=".chp_registry.json")
    cfo.add_argument(
        "--task",
        choices=[t.value for t in CFOTaskType],
        required=True,
        help="CFO task type to run.",
    )
    cfo.add_argument("--title", required=True)
    cfo.add_argument("--company", default="Aperture Corp")
    cfo.add_argument("--problem", required=True)
    cfo.add_argument("--owner", default="cfo")
    cfo.add_argument("--origin-model", default="GPT-5.4")
    cfo.add_argument("--partner-model", default="GPT-5-equivalent")
    cfo.add_argument("--partner-system", default="Partner")
    cfo.add_argument("--priority", action="append", default=[])
    cfo.add_argument("--constraint", action="append", default=[])
    cfo.add_argument("--min-runway", type=int, default=12)
    cfo.add_argument("--current-runway", type=int, default=18)

    cfo.add_argument("--base-revenue", type=float, default=0.0, help="(forecast) base revenue USD.")
    cfo.add_argument("--base-opex", type=float, default=0.0, help="(forecast) base opex USD.")
    cfo.add_argument("--growth-pct", type=float, default=0.20, help="(forecast) growth assumption decimal.")
    cfo.add_argument("--churn-pct", type=float, default=0.08, help="(forecast) churn assumption decimal.")

    cfo.add_argument("--amount", type=float, default=None, help="(investment_case) investment amount USD.")
    cfo.add_argument("--payback-months", type=int, default=None, help="(investment_case) payback months.")
    cfo.add_argument("--upside", action="append", default=[], help="(investment_case) expected upside.")
    cfo.add_argument("--risk", action="append", default=[], help="Key risk. Repeatable.")

    cfo.add_argument("--option", action="append", default=[], help="(board_output) decision option.")
    cfo.add_argument("--recommended-index", type=int, default=0)
    cfo.add_argument("--open-question", action="append", default=[])
    cfo.add_argument("--prior-decision", action="append", default=[])

    cfo.add_argument("--out-md", default=None)
    cfo.add_argument("--json", action="store_true")
    cfo.set_defaults(func=_cmd_cfo_os)

    return p


def main(argv: List[str] | None = None) -> int:
    _maybe_init_governance()  # activate EGIS before any agent runs
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
