"""
cme.governance — EGIS AI Runtime Governance for Multi-Agent CFO OS

Provides zero-code-change governance for all LLM calls made by CFO OS agents.
After ``init_governance()`` is called, every OpenAI / Anthropic / Gemini
request is automatically intercepted for PII detection, content policy
enforcement, tool-call safety, intent filtering, and audit logging.

This module is designed as a **drop-in governance layer** — existing agents
continue calling their LLM providers exactly as before. The EGIS SDK patches
supported provider libraries in-process after initialization.

Integration modes:
    1. **Auto-init from CLI** — ``cfo-os --enable-governance demo "..."``
    2. **Programmatic** — ``from cme.governance import init_governance``
    3. **Environment-only** — set ``EGISAI_API_KEY`` and the SDK activates
       on ``egisai.init()`` without any code change.

Usage:
    from cme.governance import init_governance, set_agent_context

    # Once at process start:
    init_governance()

    # Per-agent (called inside MeshAgent.act or CFOOperatingSystem.run):
    set_agent_context("finance-agent", user_id="tenant_abc")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("cme.governance")

# ── Module-level state ─────────────────────────────────────────────────────

_initialized: bool = False


def init_governance(
    api_key: Optional[str] = None,
    app_name: Optional[str] = None,
    env: Optional[str] = None,
) -> bool:
    """Initialize EGIS AI governance for the CFO OS.

    Patches supported LLM provider SDKs in-process so that every subsequent
    ``client.chat.completions.create()`` call is automatically governed.

    Parameters
    ----------
    api_key:
        EGIS API key. Falls back to ``EGISAI_API_KEY`` env var.
    app_name:
        Dashboard app label. Falls back to ``EGISAI_APP`` env var,
        then ``"multi-agent-cfo-os"``.
    env:
        Environment label. Falls back to ``EGISAI_ENV`` env var,
        then ``"production"``.

    Returns
    -------
    bool
        ``True`` if governance activated, ``False`` if SDK not importable.
    """
    global _initialized
    if _initialized:
        logger.debug("EGIS governance already initialized.")
        return True

    try:
        import egisai  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "egisai package not installed — runtime governance DISABLED. "
            "Install with: pip install 'egisai[all]'"
        )
        return False

    resolved_key = api_key or os.getenv("EGISAI_API_KEY", "")
    resolved_app = app_name or os.getenv("EGISAI_APP", "") or "multi-agent-cfo-os"
    resolved_env = env or os.getenv("EGISAI_ENV", "") or "production"

    egisai.init(
        api_key=resolved_key,
        app=resolved_app,
        env=resolved_env,
        on_block="raise",
        enable_http_fallback=True,
        quiet=False,
    )

    _initialized = True
    logger.info(
        "EGIS AI governance ACTIVATED — app=%s env=%s",
        resolved_app,
        resolved_env,
    )
    return True


def set_agent_context(
    agent_name: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Attach per-request attribution for the current agent.

    Every LLM call made after this returns will be attributed to
    ``agent_name`` on the EGIS dashboard, enabling per-agent policy
    scoping and audit isolation across Finance / Strategy / Compliance agents.

    Parameters
    ----------
    agent_name:
        Logical agent identifier matching CHP agent names
        (e.g. ``"finance-agent"``, ``"strategy-agent"``).
    user_id:
        Optional tenant / user identifier for multi-tenant isolation.
    session_id:
        Optional session identifier for request tracing.
    """
    if not _initialized:
        return

    try:
        import egisai  # type: ignore[import-untyped]

        kwargs: dict = {"agent": agent_name}
        if user_id is not None:
            kwargs["user_id"] = user_id
        if session_id is not None:
            kwargs["session_id"] = session_id

        egisai.set_context(**kwargs)
        logger.debug("EGIS context set: agent=%s", agent_name)
    except Exception as exc:
        logger.warning("Failed to set EGIS agent context: %s", exc)


def set_context_for_mesh_agent(
    agent_name: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Convenience alias: set_agent_context prefixed with 'mesh-' namespace.

    CFO OS agents operate under the Cognitive Mesh Protocol. This helper
    automatically namespaces the agent name for clearer dashboard attribution.
    """
    prefixed = f"mesh-{agent_name}"
    set_agent_context(prefixed, user_id=user_id, session_id=session_id)


def shutdown_governance() -> None:
    """Flush pending audit events and stop EGIS background workers."""
    global _initialized
    if not _initialized:
        return

    try:
        import egisai  # type: ignore[import-untyped]

        egisai.shutdown()
        _initialized = False
        logger.info("EGIS AI governance shut down cleanly.")
    except Exception as exc:
        logger.warning("EGIS shutdown error (non-fatal): %s", exc)


# ── Agent name constants (matches CHP / demo agent names) ──────────────────

AGENT_FINANCE = "finance-agent"
AGENT_STRATEGY = "strategy-agent"
AGENT_COMPLIANCE = "compliance-agent"
AGENT_ORCHESTRATOR = "orchestrator"
AGENT_CHP = "chp-engine"
AGENT_AUDIT = "audit-agent"
