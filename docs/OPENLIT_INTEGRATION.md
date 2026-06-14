# OpenLIT Integration Study

## Source: openlit/openlit (2.5K ★)
**Apache-2.0 | Python/TypeScript/Go | OpenTelemetry-native LLM Observability**

## Key Patterns for MeshCFO Observability

### 1. One-Line Instrumentation

OpenLIT wraps any LLM client with a single line:

```python
import openlit
openlit.init()  # All LLM calls are now traced
```

**MeshCFO adaptation:**
- Replace custom AuditTrail with OpenLIT for automatic LLM tracing
- Every Gemini/Bedrock call in meshcfo agents gets traced automatically
- Cost, latency, token usage captured per-call without manual instrumentation

### 2. Semantic Conventions Compliance

OpenLIT follows OpenTelemetry's GenAI semantic conventions:

```
gen_ai.system: vertex_ai
gen_ai.request.model: gemini-2.5-pro
gen_ai.usage.input_tokens: 1523
gen_ai.usage.output_tokens: 847
gen_ai.response.finish_reason: stop
```

**MeshCFO adaptation:**
- Standard trace format means meshcfo traces work with any OTel-compatible tool
- Grafana, Jaeger, Datadog all understand the trace format
- No vendor lock-in for observability

### 3. Evaluation Framework

OpenLIT includes 11 built-in evaluation types:

```
hallucination | bias | toxicity | safety | instruction_following
completeness | conciseness | sensitivity | relevance | coherence | faithfulness
```

**MeshCFO adaptation:**
- Use OpenLIT's evaluation framework to score CFO artifact quality
- Faithfulness = does the recommendation follow from the data?
- Completeness = are all CHP requirements addressed?
- This replaces your manual rubric grader with a production evaluation system

### 4. Rule Engine

OpenLIT's rule engine matches trace attributes and triggers actions:

```python
rules = [
    {"condition": "latency > 30s", "action": "alert"},
    {"condition": "cost > $5", "action": "log"},
    {"condition": "hallucination > 0.3", "action": "block"},
]
```

**MeshCFO adaptation:**
- Auto-alert when agent latency exceeds thresholds
- Block CFO artifacts with high hallucination scores
- Log all decisions above cost thresholds

## Recommended MeshCFO Changes

| Current | After OpenLIT Study |
|---------|-------------------|
| Custom AuditTrail class | OTel-native traces |
| Manual cost tracking | Automatic per-call cost |
| Custom rubric grader | OpenLIT evaluation framework |
| Ad-hoc alerting | Rule-based alerting on traces |

## Reference: OpenLIT vs AgentOps

| Feature | OpenLIT | AgentOps |
|---------|---------|----------|
| Approach | OTel-native, self-hosted | SaaS-first |
| Evaluation | 11 built-in types | Basic scoring |
| Rule engine | Conditional rules | Manual |
| Cost tracking | Automatic per-call | Per-session |
| Self-hosted | Yes (Docker) | Limited |
| Best for | Production OTel stacks | Quick integration |

**Recommendation:** Use AgentOps for quick wins (meshcfo, closedloop), OpenLIT for production observability stack.
