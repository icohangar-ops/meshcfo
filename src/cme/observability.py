"""AgentOps observability integration for MeshCFO.

Provides cost tracking, session replay, and per-agent metrics via AgentOps.
Soft dependency — if agentops is not installed, all calls are no-ops.

Usage:
    from cme.observability import init_observability, track_agent_turn

    init_observability(api_key="your-key")  # or set AGENTOPS_API_KEY env var
    # Then in your agent loop:
    with track_agent_turn("finance", "FY26 forecast") as session:
        result = agent.act(problem, shared_context=ctx)
"""
from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

_AGENTOPS_AVAILABLE = False
try:
    import agentops

    _AGENTOPS_AVAILABLE = True
except ImportError:
    pass


def init_observability(
    api_key: Optional[str] = None,
    tags: Optional[list] = None,
    default_params: Optional[Dict[str, Any]] = None,
) -> bool:
    """Initialize AgentOps for MeshCFO observability.

    Returns True if AgentOps was initialized, False otherwise.
    """
    if not _AGENTOPS_AVAILABLE:
        return False
    try:
        agentops.init(
            api_key=api_key,
            tags=tags or ["meshcfo", "multi-agent", "finance"],
            default_params=default_params or {},
        )
        return True
    except Exception:
        return False


def record_cfo_decision(
    decision_id: str,
    task_type: str,
    agents: list[str],
    total_cost_usd: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a CFO decision event for cost tracking."""
    if not _AGENTOPS_AVAILABLE:
        return
    try:
        agentops.record(
            event_type="cfo_decision",
            event_properties={
                "decision_id": decision_id,
                "task_type": task_type,
                "agents": agents,
                "total_cost_usd": total_cost_usd,
                **(metadata or {}),
            },
        )
    except Exception:
        pass


def record_chp_gate(
    decision_id: str,
    gate: str,
    score: float,
    passed: bool,
) -> None:
    """Record a CHP gate evaluation event."""
    if not _AGENTOPS_AVAILABLE:
        return
    try:
        agentops.record(
            event_type="chp_gate",
            event_properties={
                "decision_id": decision_id,
                "gate": gate,
                "score": score,
                "passed": passed,
            },
        )
    except Exception:
        pass


@contextmanager
def track_agent_turn(agent_name: str, task: str, **kwargs):
    """Context manager that tracks an agent turn with cost and timing."""
    start = time.time()
    session_data: Dict[str, Any] = {
        "agent": agent_name,
        "task": task,
        "start_time": start,
        **kwargs,
    }
    try:
        yield session_data
    finally:
        session_data["duration_s"] = time.time() - start
        if _AGENTOPS_AVAILABLE:
            try:
                agentops.record(
                    event_type="agent_turn",
                    event_properties=session_data,
                )
            except Exception:
                pass


def get_session_summary() -> Dict[str, Any]:
    """Get summary of the current observability session."""
    if not _AGENTOPS_AVAILABLE:
        return {"status": "agentops_not_installed"}
    try:
        return {
            "status": "active",
            "session_id": agentops.get_current_session_id(),
        }
    except Exception:
        return {"status": "error"}
