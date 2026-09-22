"""Multi-Agent CFO Operating System.

Capstone layer that fuses the Cognitive Mesh (Finance + Strategy + Compliance
agents on a shared ContextEngine) with the Consensus Hardening Protocol (CHP
DecisionCase, foundation hardening, lock progression) to produce three CFO-grade
artifacts with a single auditable reasoning trail:

    - ForecastPack       (driver-level forecast with stress views)
    - InvestmentCaseMemo (capital allocation case with ROI + risks)
    - BoardOutput        (decision packet for the board with locked options)

A single ``CFOOperatingSystem`` run produces:

    1. A CHP-hardened ``DecisionCase`` (foundation disclosure + attack + R0 gate
       + parity assessment + initial payload envelope).
    2. A multi-agent reasoning record (3 Mesh agent turns on shared context
       with playbook deltas).
    3. A domain-specific artifact tied back to every claim's origin.
    4. An ``AuditTrail`` linking each artifact line to an agent expansion step,
       a context entity/event, and the CHP foundation verdict.
"""

from cme.cfo_os.briefs import (
    BoardBrief,
    CFOBrief,
    CFOTaskType,
    ForecastBrief,
    InvestmentBrief,
)
from cme.cfo_os.artifacts import (
    BoardOutput,
    CFOArtifact,
    ForecastPack,
    InvestmentCaseMemo,
)
from cme.cfo_os.audit import AuditTrail, AuditEntry
from cme.cfo_os.orchestrator import CFOOperatingSystem, CFOSessionReport
from cme.cfo_os.value_pools import (
    EvidenceReference,
    GovernedValuePoolPacket,
    ValuePool,
    adapt_value_pool_packet,
)

__all__ = [
    "AuditEntry",
    "AuditTrail",
    "BoardBrief",
    "BoardOutput",
    "CFOArtifact",
    "CFOBrief",
    "CFOOperatingSystem",
    "CFOSessionReport",
    "CFOTaskType",
    "ForecastBrief",
    "ForecastPack",
    "InvestmentBrief",
    "InvestmentCaseMemo",
    "EvidenceReference",
    "GovernedValuePoolPacket",
    "ValuePool",
    "adapt_value_pool_packet",
]
