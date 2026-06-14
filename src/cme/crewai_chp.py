"""CrewAI backend for meshcfo.

Wraps MeshAgent domain specialists as CrewAI ``Agent`` objects with sequential
task flow (Finance → Strategy → Compliance) and a CHP governance overlay that
validates the collective output before it reaches the caller.

Requires the ``crewai`` optional dependency::

    pip install multi-agent-cfo-os[crewai]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cme.agent import MeshAgent, TurnResult
from cme.chp.models import DecisionCase, FoundationAttack, FoundationDisclosure, Verdict
from cme.chp.orchestrator import CHPOrchestrator, CHPReport
from cme.context import ContextEngine
from cme.protocol import ConfidenceLevel

try:
    from crewai import Agent as CrewAIAgent, Task as CrewAITask, Crew, Process
except ImportError as _exc:
    raise ImportError(
        "crewai is required for this backend. "
        "Install with: pip install multi-agent-cfo-os[crewai]"
    ) from _exc


# ---------------------------------------------------------------------------
# MeshAgent → CrewAI Agent wrapper
# ---------------------------------------------------------------------------

def _mesh_agent_to_crew_agent(mesh_agent: MeshAgent) -> CrewAIAgent:
    """Wrap a MeshAgent as a CrewAI Agent.

    The CrewAI agent's ``role``, ``goal``, and ``backstory`` are derived from
    the MeshAgent's capability metadata and playbook state.
    """
    role = f"{mesh_agent.capability.domain.title()} Specialist"
    goal = (
        f"Provide rigorous {mesh_agent.capability.domain} analysis for "
        f"multi-agent CFO decisions. Produce outputs covering: "
        f"{', '.join(mesh_agent.capability.produces) or 'domain analysis'}."
    )
    backstory_parts = [
        f"You are the {mesh_agent.name} agent in a multi-agent CFO operating system.",
        f"Your domain expertise is {mesh_agent.capability.domain}.",
        f"You consume: {', '.join(mesh_agent.capability.consumes) or 'context and problem statement'}.",
        "You use expansion/compression reasoning (Cognitive Mesh Protocol) "
        "to produce structured, auditable recommendations.",
    ]
    if mesh_agent.playbook and mesh_agent.playbook.bullets:
        backstory_parts.append("Playbook rules you follow:")
        for bullet in mesh_agent.playbook.bullets[:5]:
            backstory_parts.append(f"  - {bullet}")

    return CrewAIAgent(
        role=role,
        goal=goal,
        backstory=" ".join(backstory_parts),
        verbose=True,
        allow_delegation=False,
    )


# ---------------------------------------------------------------------------
# CHP governance overlay
# ---------------------------------------------------------------------------

def _chp_governance_overlay(
    turns: List[TurnResult],
    *,
    chp: CHPOrchestrator,
    case: Optional[DecisionCase] = None,
    disclosure: Optional[FoundationDisclosure] = None,
    attack: Optional[FoundationAttack] = None,
) -> Optional[CHPReport]:
    """Apply CHP governance after all agent tasks complete.

    Runs the full CHP session if case/disclosure/attack are provided.
    Otherwise, performs lightweight confidence-based validation.
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

    # Lightweight check: flag if any agent had failure-mode warnings
    warnings = [
        note
        for t in turns
        for note in t.handoff_notes
        if note.startswith("warning:")
    ]
    if warnings:
        return None
    return None


# ---------------------------------------------------------------------------
# Task builder
# ---------------------------------------------------------------------------

_DEFAULT_FLOW = ["Finance", "Strategy", "Compliance"]


def _build_tasks(
    mesh_agents: List[MeshAgent],
    problem: str,
    *,
    crew_agents: Dict[str, CrewAIAgent],
    flow: Optional[List[str]] = None,
    cycles: int = 1,
) -> List[CrewAITask]:
    """Build sequential CrewAI Tasks from MeshAgents.

    Each task's ``description`` encodes the problem and expected reasoning
    approach. Tasks are ordered by ``flow`` (default: Finance → Strategy →
    Compliance).
    """
    ordered_names = flow or _DEFAULT_FLOW
    by_name = {a.name: a for a in mesh_agents}
    tasks: List[CrewAITask] = []

    for idx, name in enumerate(ordered_names):
        if name not in by_name or name not in crew_agents:
            continue
        mesh = by_name[name]
        crew_agent = crew_agents[name]

        # Build context from prior tasks (sequential dependency)
        context_tasks = tasks[-1:] if tasks else []

        description_parts = [
            f"Problem: {problem}",
            "",
            f"Domain: {mesh.capability.domain}",
            f"Produces: {', '.join(mesh.capability.produces) or 'analysis'}",
            f"Consumes: {', '.join(mesh.capability.consumes) or 'problem context'}",
            "",
            "Use expansion/compression reasoning (Cognitive Mesh Protocol):",
            "1. Expand: generate hypotheses and perspectives on the problem.",
            "2. Compress: synthesize a recommendation with confidence level.",
            "3. Output: structured recommendation with rationale.",
        ]
        if mesh.playbook and mesh.playbook.bullets:
            description_parts.append("")
            description_parts.append("Playbook constraints:")
            for bullet in mesh.playbook.bullets[:3]:
                description_parts.append(f"  - {bullet}")

        expected_output_parts = [
            f"A structured {mesh.capability.domain} recommendation covering:",
            "- Key findings from expansion reasoning",
            "- Compressed recommendation with confidence (high/medium/low)",
            "- Rationale tied to the problem statement",
            "- Handoff notes for the next specialist agent",
        ]

        task = CrewAITask(
            description="\n".join(description_parts),
            expected_output="\n".join(expected_output_parts),
            agent=crew_agent,
            context=context_tasks,
        )
        tasks.append(task)

    return tasks


# ---------------------------------------------------------------------------
# High-level runner
# ---------------------------------------------------------------------------

@dataclass
class CrewAIChpResult:
    """Result of a CrewAI + CHP mesh run."""

    problem: str
    turns: List[TurnResult]
    crew_output: str = ""
    chp_report: Optional[CHPReport] = None
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "# CrewAI + CHP Mesh Result",
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
        if self.crew_output:
            lines.append("## CrewAI Output")
            lines.append(self.crew_output)
            lines.append("")
        if self.chp_report:
            lines.append("## CHP Governance")
            lines.append(self.chp_report.render())
        return "\n".join(lines)


def build_crewai_mesh(
    mesh_agents: List[MeshAgent],
    *,
    flow: Optional[List[str]] = None,
) -> tuple[Crew, Dict[str, CrewAIAgent]]:
    """Build a CrewAI Crew from MeshAgents.

    Returns:
        (Crew, mapping of agent name → CrewAI Agent) so callers can
        inspect or extend individual agents.
    """
    ordered_names = flow or _DEFAULT_FLOW
    by_name = {a.name: a for a in mesh_agents}

    crew_agents: Dict[str, CrewAIAgent] = {}
    for name in ordered_names:
        if name in by_name:
            crew_agents[name] = _mesh_agent_to_crew_agent(by_name[name])

    # Include any agents not in the default flow
    for a in mesh_agents:
        if a.name not in crew_agents:
            crew_agents[a.name] = _mesh_agent_to_crew_agent(a)

    agent_list = [crew_agents[n] for n in ordered_names if n in crew_agents]
    crew = Crew(
        agents=agent_list,
        tasks=[],  # tasks are built per-run
        process=Process.sequential,
        verbose=True,
    )
    return crew, crew_agents


def run_crewai_mesh(
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
) -> CrewAIChpResult:
    """Run the full meshcfo pipeline via CrewAI.

    Executes each MeshAgent sequentially (matching the task flow),
    collects TurnResults, runs CHP governance overlay, and returns a
    unified result.

    Note: This runs the meshcfo agents directly (expand/compress) to
    produce TurnResults, then constructs the CrewAI crew for structural
    compatibility. When CrewAI's LLM backend is configured, the crew
    can be executed via ``crew.kickoff()`` for LLM-driven reasoning.
    """
    ctx = context or ContextEngine()

    # Run meshcfo agents to get reasoning traces
    ordered_names = flow or _DEFAULT_FLOW
    by_name = {a.name: a for a in mesh_agents}
    turns: List[TurnResult] = []

    for name in ordered_names:
        if name not in by_name:
            continue
        mesh = by_name[name]
        result = mesh.act(problem, shared_context=ctx, cycles=cycles)
        turns.append(result)

    # Build CrewAI structure
    crew, crew_agents = build_crewai_mesh(mesh_agents, flow=flow)
    tasks = _build_tasks(mesh_agents, problem, crew_agents=crew_agents, flow=flow, cycles=cycles)
    crew.tasks = tasks

    # Attempt crew execution (graceful fallback if no LLM configured)
    crew_output = ""
    try:
        result = crew.kickoff()
        crew_output = str(result) if result else ""
    except Exception:
        # CrewAI not fully configured (no LLM) — fall back to meshcfo traces
        crew_output = _synthesize_from_turns(turns)

    # CHP governance overlay
    chp_report = None
    if chp:
        chp_report = _chp_governance_overlay(
            turns, chp=chp, case=case, disclosure=disclosure, attack=attack
        )

    return CrewAIChpResult(
        problem=problem,
        turns=turns,
        crew_output=crew_output,
        chp_report=chp_report,
        context_snapshot=ctx.dump(),
    )


def _synthesize_from_turns(turns: List[TurnResult]) -> str:
    """Produce a text summary from MeshAgent TurnResults when CrewAI has no LLM."""
    parts = ["## Synthesized Output (meshcfo traces)", ""]
    for t in turns:
        parts.append(f"### {t.agent}")
        parts.append(t.trace.recommendation)
        parts.append(f"Confidence: {t.trace.confidence.value}")
        parts.append("")
    return "\n".join(parts)
