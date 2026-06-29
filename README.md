<div align="center">

# MeshCFO

> **Cubiczan stack** — [Profile](https://github.com/Cubiczan) · [CHP](https://github.com/Cubiczan/consensus-hardening-protocol) · **You are here:** `meshcfo`

**The auditable multi-agent CFO.** Every board-ready claim traces to the agent that produced it, the reasoning step, the grounding source, and the adversarial review that tested it.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)](tests/)

</div>

---

## The Problem

A single CFO task — "build the FY plan", "fund the enterprise tier", "approve the board ask" — fragments across three failure modes:

1. **Context fragmentation** across departments
2. **Reasoning opacity** in the final output — nobody can trace *why* a number landed where it did
3. **Soft consensus** that looks unanimous because assumptions were never adversarially reviewed

MeshCFO solves all three. The result is a board-ready document with **complete provenance** — suitable for audit committees, board presentations, and regulatory compliance.

---

## What MeshCFO Does

Three specialist agents collaborate on a shared context engine through visible expansion/compression reasoning cycles. Every session is wrapped in the **Consensus Hardening Protocol (CHP)** — foundation disclosure, adversarial assumption attacks, R0 gate checks, and explicit lock progression.

```
brief → DecisionCase + CHP foundation disclosure + attack
      → seed shared ContextEngine
      → EnterpriseOrchestrator (Finance → Strategy → Compliance)
      → synthesize CFO artifact + AuditTrail
      → third-party validation → LOCKED
```

### The Three Agents

| Agent | Produces | Consumes |
|---|---|---|
| **Finance** | Budget envelope, ROI model | Raw brief |
| **Strategy** | Market positioning, go-to-market | Budget envelope |
| **Compliance** | Risk register, mitigations | Both |

### What Makes This Different

- **Per-claim provenance**: Every line in the final artifact traces back to the agent, expansion step, grounding source, and CHP finding
- **CHP governance**: Foundation disclosure → adversarial attack → R0 gate → lock progression (EXPLORING → PROVISIONAL_LOCK → LOCKED)
- **SpacetimeDB**: Real-time shared context, live audit trail subscriptions, zero DevOps
- **Self-improving playbooks**: Each agent owns a playbook with delta-only updates (ADD, INCREMENT, MERGE, PRUNE)

---

## Three CFO Task Types

| Task | Output | Use Case |
|------|--------|----------|
| **Forecast** | Driver-based operating plan with stress views | FY planning, budget cycles |
| **Investment Case** | Capital allocation memo with milestone-gated release | Fund requests, board approvals |
| **Board Output** | Decision packet with ranked options and dissent surface | Board meetings, committee reviews |

---

## Quick Start

```bash
git clone https://github.com/Cubiczan/meshcfo.git
cd meshcfo
pip install -e .

# Run an investment case
cfo-os cfo-os --task investment_case \
  --title "Fund enterprise tier Q3" \
  --company "Acme" \
  --problem "Should we fund a dedicated enterprise tier this quarter?" \
  --amount 4000000 \
  --payback-months 14 \
  --current-runway 18 \
  --upside "Higher ACV" --upside "Lower strategic-account churn" \
  --risk "Adoption lag" --risk "Implementation complexity"

# Run a forecast
cfo-os cfo-os --task forecast \
  --title "FY26 driver-based plan" \
  --company "Acme" \
  --problem "Build the FY26 driver-based operating plan with stress views." \
  --base-revenue 42000000 --base-opex 33000000 \
  --growth-pct 0.28 --churn-pct 0.09
```

---

## Programmatic API

```python
from cme.cfo_os import CFOOperatingSystem, InvestmentBrief
from demo import FinanceAgent, StrategyAgent, ComplianceAgent

cfo = CFOOperatingSystem(
    agents=[FinanceAgent(), StrategyAgent(), ComplianceAgent()]
)

report = cfo.run(InvestmentBrief(
    title="Fund enterprise tier",
    company="Acme",
    problem="Should we fund a dedicated enterprise tier this quarter?",
    investment_amount_usd=2_500_000,
    expected_payback_months=14,
    current_runway_months=18,
    expected_upside=["Higher ACV"],
    key_risks=["Adoption lag"],
))

print(report.case.status.value)        # PROVISIONAL_LOCK
print(report.artifact.render())        # board-ready memo
print(report.audit.render())           # per-claim provenance

# Advance to LOCKED via third-party validation
cfo.lock(
    report.case.decision_id,
    validator="fresh_instance",
    item="Investment spec v1",
    rationale="Spec coheres; flip criteria explicit.",
)
```

---

## Why SpacetimeDB?

| Before (CockroachDB) | After (SpacetimeDB) |
|---|---|
| SQLAlchemy ORM + connection pooling + query latency between agent turns | Agents connect directly as clients; state propagates instantly |
| Audit trail written to DB, visible only on query | Audit trail is a **subscription stream** — dashboards see findings appear in real-time |
| Migration scripts, schema management | Single binary, zero DevOps |
| Single-user CLI | Multi-user collaboration for free (identity system built-in) |

---

## Architecture

```
                    ┌──────────────────────────────┐
  ┌── shared ─────> │       ContextEngine           │ <───── shared ──────┐
  │                  │  (entities / events / tasks   │                      │
  │                  │   + short / long memory)       │                      │
  │                  └──────────────────────────────┘                      │
  v                                                                        v
┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────┐
│ Finance Agent       │  │ Strategy Agent     │  │ Compliance Agent      │
│  ├─ Playbook (ACE)  │  │  ├─ Playbook (ACE) │  │  ├─ Playbook (ACE)   │
│  └─ Protocol (CMP)  │  │  └─ Protocol (CMP) │  │  └─ Protocol (CMP)   │
└─────────────────────┘  └────────────────────┘  └────────────────────────┘
        │                          │                          │
        v                          v                          v
   budget_envelope          market_positioning           risk_register
        │                          │                          │
        └──────────────┬───────────┴──────────────┬──────────┘
                       v                          v
              ┌──────────────────────────────────────────┐
              │         CFOOperatingSystem               │
              │  1. CHP DecisionCase + Foundation        │
              │  2. EnterpriseOrchestrator (Mesh)        │
              │  3. Lock progression (EXPLORING > LOCK)  │
              │  4. CFOArtifact + AuditTrail             │
              └──────────────────────────────────────────┘
```

---

## Key Features

| Feature | What It Does |
|---|---|
| **Cognitive Mesh Protocol** | Visible expand/compress reasoning cycles with verified/inferred/pattern-match tagging |
| **Consensus Hardening Protocol** | Foundation disclosure → adversarial attack → R0 gate → lock progression |
| **Self-Improving Playbooks** | Delta-only updates after every turn (ADD, INCREMENT, MERGE, PRUNE) |
| **Per-Claim Audit Trail** | Every line traces to agent + grounding + CHP finding |
| **Context Engine** | Entity/event/task graph with cosine dedup and combined scoring (semantic 50%, recency 20%, importance 20%, frequency 10%) |
| **SpacetimeDB** | Real-time subscriptions, live audit streams, zero DevOps |

---

## Project Structure

```
meshcfo/
├── src/
│   ├── cme/
│   │   ├── cli.py              # CLI entry point
│   │   ├── agent.py            # MeshAgent base class
│   │   ├── protocol.py         # Cognitive Mesh Protocol
│   │   ├── context.py          # ContextEngine
│   │   ├── playbook.py         # Playbook + Reflector + Curator
│   │   ├── chp/                # Consensus Hardening Protocol
│   │   ├── cfo_os/             # CFOOperatingSystem capstone
│   │   └── db/                 # SpacetimeDB persistence
│   └── demo/                   # Finance, Strategy, Compliance agents
├── spacetime/                  # SpacetimeDB Rust module + Python client
├── tests/                      # pytest suite
└── examples/                   # CLI invocations
```

---

## Running Tests

```bash
pip install pytest
PYTHONPATH=src pytest tests/ -v
```

---

## Integrations and Inspirations

MeshCFO is part of a growing ecosystem of multi-agent systems. These complementary projects are worth exploring:

- **[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)** (86K stars) – Multi-agent financial trading framework.
- **[openai/openai-agents-python](https://github.com/openai/openai-agents-python)** (27K stars) – OpenAI's official agent framework.
- **[crewAIInc/crewAI](https://github.com/crewAIInc/crewAI)** (53K stars) – Role-based AI agent orchestration.
- **[AgentOps-AI/agentops](https://github.com/AgentOps-AI/agentops)** (5.6K stars) – Agent observability and analytics.
- **[raga-ai-hub/RagaAI-Catalyst](https://github.com/raga-ai-hub/RagaAI-Catalyst)** (16K stars) – Agent tracing and evaluation platform.

---

---

## Cubiczan stack

| Finance | [Strata](https://github.com/Cubiczan/Strata) · [Metabocommand](https://github.com/Cubiczan/Metabocommand) · [meshcfo](https://github.com/Cubiczan/meshcfo) · [working-capital-optimizer](https://github.com/Cubiczan/working-capital-optimizer) · [cash-flow-optimizer](https://github.com/Cubiczan/cash-flow-optimizer) · [finance-cockpit](https://github.com/Cubiczan/finance-cockpit) |
| Governance | [consensus-hardening-protocol](https://github.com/Cubiczan/consensus-hardening-protocol) · [agent-conductor](https://github.com/Cubiczan/agent-conductor) · [compliance-as-code-agent](https://github.com/Cubiczan/compliance-as-code-agent) · [cleanmandate](https://github.com/Cubiczan/cleanmandate) |

MeshCFO wraps every CFO session in **CHP** — pair with [Strata](https://github.com/Cubiczan/Strata) for maturity roadmaps and [Metabocommand](https://github.com/Cubiczan/Metabocommand) for operational approval queues.

## License

MIT. See [`LICENSE`](./LICENSE).
