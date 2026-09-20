"""CFOOperatingSystem — fuses Mesh agents with CHP hardening.

A single ``run(brief)`` does the following:

    1. Builds a CHP ``DecisionCase`` + ``FoundationDisclosure`` + ``FoundationAttack``
       from the brief.
    2. Seeds the shared ``ContextEngine`` with the brief and the dossier so all
       three agents read from the same organizational view.
    3. Runs the ``EnterpriseOrchestrator`` (Finance -> Strategy -> Compliance,
       topologically sorted) so each agent contributes a reasoning trace and
       playbook deltas on shared context.
    4. Advances the CHP session: R0 gate, foundation verdict, parity assessment,
       initial payload envelope. If foundation passes and no failure modes
       triggered, the session advances to ``PROVISIONAL_LOCK``.
    5. Synthesizes a domain-specific CFO artifact tied back to every claim's
       origin via an ``AuditTrail``.

The output is a ``CFOSessionReport`` that renders to a single board-ready
markdown document.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cme.agent import MeshAgent, TurnResult
from cme.audit import AuditLedger
from cme.bridge import EntryPoint
from cme.chp.foundation import foundation_verdict, validate_foundation_pair
from cme.chp.gates import evaluate_r0_gate
from cme.chp.models import (
    DecisionCase,
    FoundationAttack,
    FoundationDisclosure,
    SessionStatus,
    ThirdPartyValidation,
    ValidationResult,
    Verdict,
)
from cme.chp.orchestrator import CHPOrchestrator
from cme.chp.parity import assess_model_parity
from cme.chp.payloads import build_payload_envelope
from cme.chp.registry import DecisionRegistry
from cme.chp.validators import apply_third_party_validation
from cme.context import ContextEngine, Entity, Task
from cme.hardening import ChpDecisionGate, ChpRejection
from cme.orchestrator import EnterpriseOrchestrator, OrchestrationReport

from cme.cfo_os.artifacts import (
    BoardOutput,
    CFOArtifact,
    ForecastPack,
    InvestmentCaseMemo,
    build_board_output,
    build_forecast_pack,
    build_investment_case_memo,
)
from cme.cfo_os.audit import AuditTrail, build_audit_trail
from cme.cfo_os.briefs import BoardBrief, CFOBrief, CFOTaskType, ForecastBrief, InvestmentBrief
from cme.cfo_os.dossier_builders import build_decision_case


@dataclass
class CFOSessionReport:
    brief: CFOBrief
    case: DecisionCase
    foundation_disclosure: FoundationDisclosure
    foundation_attack: FoundationAttack
    r0_verdict: Verdict
    foundation_verdict: Verdict
    initial_packet: str
    orchestration: OrchestrationReport
    artifact: CFOArtifact
    audit: AuditTrail
    turns: List[TurnResult] = field(default_factory=list)
    hardening: Optional[Dict[str, Any]] = None

    def render(self) -> str:
        sections = [
            "# CFO OS Session",
            f"**Task:** {self.brief.task_type.value}",
            f"**Title:** {self.brief.title}",
            f"**Company:** {self.brief.company}",
            f"**Lock state:** `{self.case.status.value}`",
            f"**Foundation score:** {self.case.foundation_score}  ·  "
            f"R0: `{self.r0_verdict.value}`  ·  Foundation: `{self.foundation_verdict.value}`",
            "",
            self.artifact.render(),
            "",
            self.audit.render(),
            "",
        ]
        if self.hardening:
            parity = self.hardening.get("parity")
            parity_line = (
                f"- golden parity: {parity['case_id']} ({parity['metric']}) expected "
                f"{parity['expected']} {parity['unit']}, got {parity['actual']} — "
                + ("within tolerance" if parity["within_tolerance"] else "MISMATCH")
                if parity
                else "- golden parity: no golden QA case matched this brief"
            )
            sections.extend(
                [
                    "## CHP Decision Gate",
                    f"- decision_id: `{self.hardening['decision_id']}`  ·  session status: "
                    f"**{self.hardening['session_status']}**",
                    f"- gate R0: `{self.hardening['r0_verdict']}`  ·  gate foundation: "
                    f"`{self.hardening['foundation_verdict']}`  ·  score: "
                    f"{self.hardening['foundation_score']}  ·  domain: {self.hardening['domain']}",
                    parity_line,
                    f"- confirmed_by: {self.hardening['confirmed_by'] or '(pending human confirmation)'}",
                    "",
                ]
            )
        sections.extend(
            [
                "## Initial CHP Packet",
                "```",
                self.initial_packet,
                "```",
                "",
                "## Mesh Orchestration Detail",
                self.orchestration.render(),
            ]
        )
        return "\n".join(sections)


class CFOOperatingSystem:
    """High-level CFO orchestrator. Mesh agents + CHP hardening on shared context."""

    def __init__(
        self,
        *,
        agents: List[MeshAgent],
        registry: Optional[DecisionRegistry] = None,
        context: Optional[ContextEngine] = None,
        company_name: str = "Aperture Corp",
        ledger: Optional[AuditLedger] = None,
        gate: Optional[ChpDecisionGate] = None,
    ) -> None:
        if not agents:
            raise ValueError("CFOOperatingSystem requires at least one MeshAgent")
        self.agents = agents
        self.registry = registry or DecisionRegistry()
        self.context = context or ContextEngine()
        self.company_name = company_name
        # One signed ledger for the whole session (mesh turns + final artifact).
        self.ledger = ledger if ledger is not None else AuditLedger()
        # CHP decision gate (consensus-hardening-protocol): R0 before the
        # orchestration, deterministic adversary over the produced artifact,
        # human lock, and the append-only decision ledger.
        self.gate = gate if gate is not None else ChpDecisionGate()
        self._chp = CHPOrchestrator(registry=self.registry, context=self.context)
        self._mesh = EnterpriseOrchestrator(
            agents=self.agents, context=self.context, ledger=self.ledger
        )

    # --- Public API ------------------------------------------------------

    def run(self, brief: CFOBrief, *, confirmed_by: Optional[str] = None) -> CFOSessionReport:
        case, disclosure, attack = build_decision_case(brief)

        # CHP R0 — before the engine: an ill-posed brief is refused before any
        # agent runs, with nothing executed or persisted.
        self._chp_guarded(brief, case, lambda: self.gate.open_r0(brief, case))

        self._seed_context(brief, case)

        chp_report = self._chp.run_initial_session(
            case=case,
            foundation_disclosure=disclosure,
            foundation_attack=attack,
        )

        orchestration = self._mesh.orchestrate(
            brief.problem,
            entry_point=EntryPoint.PROBLEM,
            workflow_title=f"{brief.task_type.value}: {brief.title[:60]}",
        )

        self._advance_lock_state(chp_report.case, chp_report.foundation_verdict, orchestration.turns)

        artifact = self._build_artifact(brief, chp_report.case, orchestration.turns)

        # CHP foundation pass — the deterministic adversary scores the produced
        # artifact (guardrails 40 + bounded result 30 + golden parity 30).
        decision = self._chp_guarded(
            brief,
            case,
            lambda: self.gate.harden(
                brief=brief,
                case=case,
                disclosure=disclosure,
                artifact=artifact,
                orchestration=orchestration,
            ),
        )

        # Human lock policy — refusals land before anything durable is written.
        self._chp_guarded(
            brief, case, lambda: self.gate.enforce_lock_policy(decision, confirmed_by)
        )
        if confirmed_by:
            self.gate.lock(decision, confirmed_by)

        audit = build_audit_trail(
            turns=orchestration.turns,
            case=chp_report.case,
            disclosure=disclosure,
            attack=attack,
        )

        # Sign the top-level board-ready artifact into the tamper-evident ledger.
        self.ledger.append(
            event=f"cfo_artifact:{brief.task_type.value}",
            actor="cfo_os",
            inputs={"decision_id": chp_report.case.decision_id,
                    "title": brief.title, "company": brief.company,
                    "task_type": brief.task_type.value},
            sources=[e.grounding_source for e in audit.entries],
            confidence=chp_report.foundation_verdict.value,
            rationale=f"lock_state={chp_report.case.status.value}; "
            f"foundation_score={chp_report.case.foundation_score}",
        )
        # Seal the CHP decision record into the append-only decision ledger.
        record = self.gate.record(
            decision,
            brief=brief,
            artifact=artifact,
            orchestration=orchestration,
            confirmed_by=confirmed_by,
        )

        hardening = {
            "decision_id": decision.case.decision_id,
            "session_status": decision.case.status.value,
            "r0_verdict": decision.report.r0_verdict.value,
            "foundation_verdict": decision.report.foundation_verdict.value,
            "foundation_score": decision.case.foundation_score,
            "domain": decision.assessment.domain,
            "parity": decision.assessment.parity.to_dict()
            if decision.assessment.parity
            else None,
            "confirmed_by": confirmed_by,
            "ledger_body_sha256": record["body_sha256"],
        }

        return CFOSessionReport(
            brief=brief,
            case=chp_report.case,
            foundation_disclosure=disclosure,
            foundation_attack=attack,
            r0_verdict=chp_report.r0_verdict,
            foundation_verdict=chp_report.foundation_verdict,
            initial_packet=chp_report.initial_packet,
            orchestration=orchestration,
            artifact=artifact,
            audit=audit,
            turns=orchestration.turns,
            hardening=hardening,
        )

    def _chp_guarded(self, brief: CFOBrief, case: DecisionCase, step):
        """Run a CHP gate stage; audit the refusal, then re-raise."""
        try:
            return step()
        except ChpRejection as exc:
            self.ledger.append(
                event="chp_rejected",
                actor="cfo_os",
                inputs={
                    "decision_id": case.decision_id,
                    "task_type": brief.task_type.value,
                    "title": brief.title,
                },
                sources=[],
                confidence="HALT",
                rationale=exc.reason,
            )
            raise

    def lock(
        self,
        decision_id: str,
        *,
        validator: str,
        item: str,
        rationale: str,
        challenge: str = "Stress test before lock progression.",
        confirm: bool = True,
    ) -> DecisionCase:
        """Apply a third-party validation; advance lock state."""
        case = self.registry.get(decision_id)
        if not case:
            raise KeyError(f"Unknown decision_id: {decision_id}")
        validation = ThirdPartyValidation(
            validator=validator,
            item=item,
            challenge=challenge,
            result=ValidationResult.CONFIRM if confirm else ValidationResult.REJECT,
            rationale=rationale,
        )
        apply_third_party_validation(case, validation)
        # Durable decision-ledger event: a post-run lock (or rejection) is
        # appended so the ledger reflects the case's current lock state.
        self.gate.record_lock(
            decision_id=decision_id,
            session_status=case.status.value,
            confirmed_by=validator,
            rationale=rationale,
        )
        return case

    # --- Internals -------------------------------------------------------

    def _seed_context(self, brief: CFOBrief, case: DecisionCase) -> None:
        self.context.upsert_entity(
            Entity(
                id="org",
                type="org",
                attributes={"name": brief.company or self.company_name, "horizon": brief.horizon},
            )
        )
        self.context.upsert_entity(
            Entity(
                id=case.decision_id,
                type="decision_case",
                attributes={
                    "title": case.title,
                    "domain": case.domain,
                    "owner": case.owner,
                    "high_stakes": case.high_stakes,
                    "task_type": brief.task_type.value,
                },
            )
        )
        if case.dossier:
            for i, c in enumerate(case.dossier.constraints[:6]):
                self.context.upsert_entity(
                    Entity(
                        id=f"{case.decision_id}-constraint-{i}",
                        type="constraint",
                        attributes={"text": c},
                    )
                )
        self.context.add_task(
            Task(
                id=f"task-{case.decision_id}",
                goal=f"Harden {brief.task_type.value}: {brief.title}",
                status="in_progress",
                owner=case.owner,
            )
        )
        self.context.record_event(
            actor="cfo_os",
            action="session_open",
            object_=case.decision_id,
        )

    def _advance_lock_state(
        self, case: DecisionCase, f_verdict: Verdict, turns: List[TurnResult]
    ) -> None:
        # Foundation has to pass and no failure mode in any turn for provisional lock.
        any_failure = any(
            note.startswith("warning:") for t in turns for note in t.handoff_notes
        )
        # Don't downgrade EXISTING harder states (HALT / REFRAME) set upstream.
        if case.status in {SessionStatus.HALT, SessionStatus.REFRAME_REQUIRED}:
            return
        if f_verdict == Verdict.PASS and not any_failure:
            case.status = SessionStatus.PROVISIONAL_LOCK

    def _build_artifact(
        self, brief: CFOBrief, case: DecisionCase, turns: List[TurnResult]
    ) -> CFOArtifact:
        if isinstance(brief, ForecastBrief):
            return build_forecast_pack(brief=brief, case=case, turns=turns)
        if isinstance(brief, InvestmentBrief):
            return build_investment_case_memo(brief=brief, case=case, turns=turns)
        if isinstance(brief, BoardBrief):
            return build_board_output(brief=brief, case=case, turns=turns)
        raise TypeError(f"Unsupported brief type: {type(brief).__name__}")

    # Re-expose CHP primitives for callers that want raw access ----------

    @staticmethod
    def assess_parity(origin_model: str, partner_model: str):
        return assess_model_parity(origin_model, partner_model)

    @staticmethod
    def evaluate_r0(case: DecisionCase) -> Verdict:
        return evaluate_r0_gate(
            solvable=True,
            scoped=bool(case.dossier and case.dossier.scope),
            valid=bool(case.dossier and case.dossier.current_state),
            worth_it=case.high_stakes,
        ).verdict

    @staticmethod
    def validate_foundation(
        disclosure: FoundationDisclosure, attack: FoundationAttack
    ) -> List[str]:
        return validate_foundation_pair(disclosure, attack)

    @staticmethod
    def envelope(body: str) -> str:
        return build_payload_envelope(body).render()

    @staticmethod
    def foundation_pass(attack: FoundationAttack) -> bool:
        return foundation_verdict(attack) == Verdict.PASS
