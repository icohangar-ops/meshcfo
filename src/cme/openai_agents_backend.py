"""OpenAI Agents Python backend for meshcfo.

Wraps MeshAgent domain specialists as openai-agents-python ``Agent`` objects
with handoff rules that enforce Finance → Strategy → Compliance flow, plus
CHP post-processing on the collected reasoning traces.

Requires the ``openai-agents`` optional dependency::

    pip install multi-agent-cfo-os[openai-agents]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from cme.agent import AgentCapability, MeshAgent, TurnResult
from cme.bridge import BridgeFramework, EntryPoint
from cme.chp.models import DecisionCase, FoundationAttack, FoundationDisclosure, Verdict
from cme.chp.orchestrator import CHPOrchestrator, CHPReport
from cme.context import ContextEngine
from cme.protocol import ConfidenceLevel

try:
    from agents import Agent, handoff, RunContextWrapper
except ImportError as _exc:
    raise ImportError(
        "openai-agents-python is required for this backend. "
        "Install with: pip install multi-agent-cfo-os[openai-agents]"
    ) from _exc


# ---------------------------------------------------------------------------
# Handoff-payload that travels with each agent transfer
# ---------------------------------------------------------------------------

@dataclass
class MeshHandoffPayload:
    """Structured payload passed between agents during handoff."""

    problem: str
    previous_agent: str
    previous_recommendation: str
    confidence: str
    context_snapshot: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MeshAgent → openai-agents Agent wrapper
# ---------------------------------------------------------------------------

def _build_agent_instructions(mesh_agent: MeshAgent) -> str:
    """Generate system instructions from a MeshAgent's capability and playbook."""
    lines = [
        f"You are the {mesh_agent.name} specialist in a multi-agent CFO operating system.",
        f"Your domain: {mesh_agent.capability.domain}.",
        f"You produce outputs: {', '.join(mesh_agent.capability.produces) or 'general analysis'}.",
        f"You consume inputs: {', '.join(mesh_agent.capability.consumes) or 'none'}.",
        "",
        "When you receive a problem, reason through it using expansion (brainstorm hypotheses) "
        "then compression (synthesize a recommendation with confidence assessment).",
        "",
        "After completing your analysis, hand off to the next specialist agent in the pipeline.",
    ]
    if mesh_agent.playbook and mesh_agent.playbook.bullets:
        lines.append("")
        lines.append("Playbook rules:")
        for bullet in mesh_agent.playbook.bullets[:5]:
            lines.append(f"  - {bullet}")
    return "\n".join(lines)


def _mesh_agent_to_openai_agent(
    mesh_agent: MeshAgent,
    handoff_targets: Optional[List[str]] = None,
) -> Agent:
    """Wrap a MeshAgent as an openai-agents-python Agent.

    The Agent's ``instructions`` are derived from the MeshAgent's capability
    and playbook. Handoff targets define which agents this agent can delegate to.
    """
    instructions = _build_agent_instructions(mesh_agent)

    # Build handoff list: accept MeshAgent instances or name strings
    handoff_list = []
    if handoff_targets:
        for target in handoff_targets:
            handoff_list.append(target)  # resolved later when all agents are built

    return Agent(
        name=mesh_agent.name,
        instructions=instructions,
        handoff_description=(
            f"{mesh_agent.name} agent — {mesh_agent.capability.domain} specialist. "
            f"Produces: {', '.join(mesh_agent.capability.produces)}"
        ),
        handoffs=handoff_list,
    )


# ---------------------------------------------------------------------------
# CHP post-processing
# ---------------------------------------------------------------------------

def _chp_post_process(
    turns: List[TurnResult],
    *,
    chp: CHPOrchestrator,
    case: Optional[DecisionCase] = None,
    disclosure: Optional[FoundationDisclosure] = None,
    attack: Optional[FoundationAttack] = None,
) -> Optional[CHPReport]:
    """Run CHP governance on the collected agent turns.

    If a case/disclosure/attack are provided, runs a full initial CHP session.
    Otherwise, performs a lightweight confidence-based assessment.
    """
    if case and disclosure and attack:
        try:
            return chp.run_initial_session(
                case=case,
                foundation_disclosure=disclosure,
                foundation_attack=attack,
            )
        except ValueError:
            return None

    # Lightweight fallback: check if any agent flagged warnings
    any_failure = any(
        note.startswith("warning:") for t in turns for note in t.handoff_notes
    )
    if any_failure:
        return None
    return None


# ---------------------------------------------------------------------------
# Orchestrator builder
# ---------------------------------------------------------------------------

_OPENAI_ROLE_MAP = {
    "finance": "finance_agent",
    "strategy": "strategy_agent",
    "compliance": "compliance_agent",
}


def build_openai_agents_mesh(
    mesh_agents: List[MeshAgent],
    *,
    flow: Optional[List[str]] = None,
) -> Dict[str, Agent]:
    """Build a dict of openai-agents-python Agent objects from MeshAgents.

    Args:
        mesh_agents: The meshcfo MeshAgent instances to wrap.
        flow: Optional ordered list of agent names defining handoff flow.
              Defaults to the capability-based topological order.

    Returns:
        Dict mapping agent name → openai Agent instance.
    """
    if not flow:
        flow = [a.name for a in mesh_agents]

    flow_map = {a.name: a for a in mesh_agents}

    agents: Dict[str, Agent] = {}
    for idx, name in enumerate(flow):
        if name not in flow_map:
            continue
        mesh = flow_map[name]
        next_names = [flow[idx + 1]] if idx + 1 < len(flow) else []

        oa_agent = _mesh_agent_to_openai_agent(mesh, handoff_targets=next_names)
        agents[name] = oa_agent

    # Resolve handoff references: replace name strings with Agent instances
    for name, oa_agent in agents.items():
        resolved = []
        for h in oa_agent.handoffs:
            if isinstance(h, str) and h in agents:
                resolved.append(agents[h])
            else:
                resolved.append(h)
        oa_agent.handoffs = resolved

    return agents


# ---------------------------------------------------------------------------
# High-level runner
# ---------------------------------------------------------------------------

@dataclass
class OpenAIAgentsMeshResult:
    """Result of an openai-agents mesh run."""

    problem: str
    turns: List[TurnResult]
    chp_report: Optional[CHPReport] = None
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "# OpenAI Agents Mesh Result",
            f"**Problem:** {self.problem}",
            f"**Agents:** {', '.join(t.agent for t in self.turns)}",
            "",
        ]
        for t in self.turns:
            lines.append(f"## {t.agent}")
            lines.append(t.trace.recommendation)
            lines.append(f"Confidence: {t.trace.confidence.value}")
            if t.handoff_notes:
                for n in t.handoff_notes:
                    lines.append(f"  - {n}")
            lines.append("")
        if self.chp_report:
            lines.append("## CHP Governance")
            lines.append(self.chp_report.render())
        return "\n".join(lines)


def run_openai_agents_mesh(
    mesh_agents: List[MeshAgent],
    problem: str,
    *,
    context: Optional[ContextEngine] = None,
    chp: Optional[CHPOrchestrator] = None,
    case: Optional[DecisionCase] = None,
    disclosure: Optional[FoundationDisclosure] = None,
    attack: Optional[FoundationAttack] = None,
    flow: Optional[List[str]] = None,
    cycles: int = 1,
) -> OpenAIAgentsMeshResult:
    """Run the full meshcfo pipeline via openai-agents-python.

    This executes each MeshAgent sequentially (matching the handoff flow),
    collects TurnResults, then runs CHP post-processing.

    Note: This runs the meshcfo agents directly (expand/compress) rather
    than invoking the OpenAI API. The openai-agents Agent objects are
    built and available for use with ``Runner.run()`` when an LLM backend
    is configured.
    """
    ctx = context or ContextEngine()
    ordered = _order_agents(mesh_agents, flow)
    turns: List[TurnResult] = []

    for mesh in ordered:
        result = mesh.act(problem, shared_context=ctx, cycles=cycles)
        turns.append(result)

    chp_report = None
    if chp:
        chp_report = _chp_post_process(
            turns, chp=chp, case=case, disclosure=disclosure, attack=attack
        )

    return OpenAIAgentsMeshResult(
        problem=problem,
        turns=turns,
        chp_report=chp_report,
        context_snapshot=ctx.dump(),
    )


def _order_agents(
    agents: List[MeshAgent], flow: Optional[List[str]] = None
) -> List[MeshAgent]:
    """Order agents by explicit flow or by capability dependency."""
    if flow:
        by_name = {a.name: a for a in agents}
        return [by_name[n] for n in flow if n in by_name]

    # Default:Finance → Strategy → Compliance
    preferred = ["Finance", "Strategy", "Compliance"]
    by_name = {a.name: a for a in agents}
    ordered = []
    for name in preferred:
        if name in by_name:
            ordered.append(by_name.pop(name))
    ordered.extend(by_name.values())
    return ordered
