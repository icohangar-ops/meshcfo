# SpacetimeDB Module — Multi-Agent CFO OS

Real-time backend module for the **Multi-Agent CFO Operating System**,
powered by [SpacetimeDB](https://spacetimedb.com) (v2.4.1).

The module lives in ``./spacetimedb/`` (Rust) and exposes a Python SDK
client at ``./client.py`` that the ``cme`` package can use directly.

---

## Architecture

```
┌─────────────────────┐     HTTP / SSE      ┌──────────────────┐
│  Agent A (Finance)  │─────────────────────▶│                  │
├─────────────────────┤                      │  SpacetimeDB     │
│  Agent B (Strategy) │─────────────────────▶│  Gateway         │
├─────────────────────┤                      │  :3000           │
│  Agent C (Comply)   │─────────────────────▶│                  │
└─────────────────────┘                      └────────┬─────────┘
                                                       │
                                              ┌────────▼─────────┐
                                              │  cfo_os.wasm     │
                                              │  (Rust module)   │
                                              │                  │
                                              │  Tables:         │
                                              │  • Brief         │
                                              │  • AgentTurnRec  │
                                              │  • SharedCtxEnt  │
                                              │  • DecisionCase  │
                                              │  • AuditEntry    │
                                              │  • FinalArtifact │
                                              └──────────────────┘
```

## Tables

| Table                | Description                                      |
|----------------------|--------------------------------------------------|
| `Brief`              | Input briefs for a new CFO analysis session.     |
| `AgentTurnRecord`    | Per-agent reasoning-cycle records.               |
| `SharedContextEntity`| Entity / Event / Task shared across agents.      |
| `DecisionCase`       | CHP-hardened decision state machine.             |
| `AuditEntry`         | Per-claim audit trail with CHP findings.         |
| `FinalArtifact`      | Rendered output (forecast pack, memo, board deck).|

## Reducers

| Reducer               | Purpose                                          |
|-----------------------|--------------------------------------------------|
| `submit_brief`        | Kick off a new analysis.                         |
| `record_agent_turn`   | Agent reports a reasoning cycle.                 |
| `update_decision_state`| Advance CHP state machine (Exploring → Locked). |
| `publish_artifact`    | Publish final rendered output.                   |
| `seed_briefs`         | (dev helper) Insert sample briefs.               |

---

## Build

```bash
cd spacetime
spacetime build
```

The compiled WASM module is produced at
``spacetimedb/target/wasm32-unknown-unknown/release/cfo_os.wasm``.

## Publish (local)

```bash
spacetime start            # start a local SpacetimeDB instance
spacetime publish cfo_os   # publish the module
```

## Publish (production / maincloud)

```bash
spacetime login
spacetime publish --project <project-id> cfo_os
```

---

## Python SDK

``client.py`` provides a pure-Python HTTP client.

```python
from client import SpacetimeClient

client = SpacetimeClient()

# Submit a brief
brief_id = client.submit_brief(
    title="FY2027 Revenue Forecast",
    company="AcmeCorp",
    problem="Revenue under three scenarios",
    task_type="Forecast",
)

# Record an agent turn
client.record_turn(
    brief_id=brief_id,
    agent_name="Finance",
    status="Complete",
    expansion_text="...",
    compression_text="summary",
    confidence=0.87,
)

# Publish final artifact
client.publish_artifact(
    brief_id=brief_id,
    rendered_markdown="# Forecast Results\n\nRevenue: $120M...",
    version=1,
)

# Query artifacts
for art in client.get_artifacts(brief_id):
    print(art["rendered_markdown"][:100])

# Live audit subscription (blocking — use in background thread)
def on_audit(entry):
    print(f"AUDIT: {entry.claim} — {entry.chp_finding}")

# client.subscribe_audit(decision_id="d-abc", callback=on_audit)
```

### Quick test

```bash
python client.py health
python client.py seed
```

Requirements: ``requests`` (install with ``pip install requests``).

---

## Migration from CockroachDB

If you have an existing CockroachDB deployment with a JSON dump of
brief records, you can migrate them in bulk:

```python
client = SpacetimeClient()
count = client.migrate_from_cockroach("dump.json")
print(f"Migrated {count} briefs")
```

The expected JSON format is either a JSON array:

```json
[
  {"brief_id": "...", "title": "...", "company": "...", ...},
  ...
]
```

Or newline-delimited JSON (one object per line).
