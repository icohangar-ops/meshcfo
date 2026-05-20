"""MeshAgent base class.

A MeshAgent wraps a domain specialization (finance, strategy, legal, ...) and
uses the four subsystems:

    protocol  -> expansion/compression reasoning on each turn
    context   -> shared organizational knowledge (read relevant, write insights)
    playbook  -> evolving, self-improving behavioural rules
    reflector -> produce insights from each turn to feed the curator

Subclasses implement ``expand`` and ``compress`` — the rest is handled by the
framework. Each turn emits a ``TurnResult`` which the orchestrator passes to
the Bridge Framework.

Governance: When EGIS AI is installed and activated, every LLM call made
during agent execution is automatically governed (PII masking, content
policies, tool-call safety, audit trail). Agent attribution is set via
``set_context_for_mesh_agent`` before each turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cme.context import ContextEngine
from cme.playbook import Curator, DeltaOp, Playbook, Reflector
from cme.protocol import (
    CognitiveMeshProtocol,
    CompressionStep,
    ConfidenceLevel,
    ExpansionStep,
    ReasoningTrace,
)


def _try_set_governance_context(agent_name: str) -> None:
    """Set EGIS governance context for the current agent, if available.

    This is a soft dependency — if egisai is not installed or governance
    was not initialized, this is a silent no-op.
    """
    try:
        from cme.governance import set_context_for_mesh_agent  # noqa: WPS433

        set_context_for_mesh_agent(agent_name)
    except Exception:
        pass  # governance not available — proceed without it


@dataclass
class AgentCapability:
    domain: str
    produces: List[str] = field(default_factory=list)  # output keys it emits
    consumes: List[str] = field(default_factory=list)  # expected inputs


@dataclass
class TurnResult:
    agent: str
    trace: ReasoningTrace
    deltas_applied: List[str]
    outputs: Dict[str, Any]
    handoff_notes: List[str] = field(default_factory=list)


class MeshAgent:
    def __init__(
        self,
        name: str,
        capability: AgentCapability,
        *,
        protocol: Optional[CognitiveMeshProtocol] = None,
        playbook: Optional[Playbook] = None,
    ) -> None:
        self.name = name
        self.capability = capability
        self.protocol = protocol or CognitiveMeshProtocol()
        self.playbook = playbook or Playbook(name=f"{name}-playbook")
        self._reflector = Reflector()
        self._curator = Curator()

    # Subclasses override ---------------------------------------------------

    def expand(self, problem: str, context: Dict[str, Any]) -> List[ExpansionStep]:
        raise NotImplementedError

    def compress(
        self,
        problem: str,
        expansion: List[ExpansionStep],
        context: Dict[str, Any],
    ) -> "tuple[str, List[CompressionStep], ConfidenceLevel, str, Dict[str, Any]]":
        """Return (recommendation, compression_steps, confidence, what_would_change, outputs)."""
        raise NotImplementedError

    # ---------------------------------------------------------------------

    def act(
        self,
        problem: str,
        *,
        shared_context: ContextEngine,
        cycles: int = 1,
    ) -> TurnResult:
        # Set EGIS governance context for this agent
        _try_set_governance_context(self.name)

        ctx_snapshot = shared_context.snapshot_for(self.name, problem, k=6)

        # Expand + compress via protocol
        outputs_holder: Dict[str, Any] = {}

        def _compress(p: str, exp: List[ExpansionStep], ctx: Dict[str, Any]):
            rec, steps, conf, wwc, outs = self.compress(p, exp, ctx)
            outputs_holder.update(outs)
            return rec, steps, conf, wwc

        trace = self.protocol.run(
            problem,
            expansion_fn=self.expand,
            compression_fn=_compress,
            context=ctx_snapshot,
            cycles=cycles,
        )

        # Write recommendation back to shared context
        shared_context.write(
            content=f"[{self.name}] {trace.recommendation}",
            source_agent=self.name,
            importance=self._importance_from_confidence(trace.confidence),
            tags=[self.capability.domain, "recommendation"],
        )
        shared_context.record_event(
            actor=self.name,
            action="recommend",
            object_=problem[:60],
            confidence=trace.confidence.value,
        )

        # Reflect + curate -> playbook delta
        outcome = (
            "failure"
            if self.protocol.detect_failure_mode(trace)
            else ("partial" if trace.confidence == ConfidenceLevel.LOW else "success")
        )
        reflection = self._reflector.reflect(
            trajectory_summary=trace.recommendation,
            outcome=outcome,
            current_playbook=self.playbook,
            grounding_issues=[g.risk_flag for g in trace.grounding if g.risk_flag],
        )
        ops: List[DeltaOp] = self._curator.curate(reflection, self.playbook)
        changelog = self.playbook.apply(ops)

        handoff_notes = [
            f"confidence={trace.confidence.value}",
            f"produces={self.capability.produces}",
        ]
        failure = self.protocol.detect_failure_mode(trace)
        if failure:
            handoff_notes.append(f"warning:{failure}")

        return TurnResult(
            agent=self.name,
            trace=trace,
            deltas_applied=changelog,
            outputs=outputs_holder,
            handoff_notes=handoff_notes,
        )

    @staticmethod
    def _importance_from_confidence(c: ConfidenceLevel) -> float:
        return {
            ConfidenceLevel.HIGH: 0.85,
            ConfidenceLevel.MEDIUM: 0.6,
            ConfidenceLevel.LOW: 0.3,
        }[c]
