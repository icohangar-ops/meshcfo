"""Governed adapters for evidence-backed procurement and working-capital value pools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping


ALLOWED_DOMAINS = {"procurement", "working_capital"}


@dataclass(frozen=True)
class EvidenceReference:
    """A traceable source supporting a value-pool estimate."""

    source_id: str
    source_type: str
    locator: str = ""
    as_of: str = ""
    confidence: float = 0.0

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.source_id:
            errors.append("evidence.source_id is required")
        if not self.source_type:
            errors.append("evidence.source_type is required")
        if not 0 <= self.confidence <= 1:
            errors.append("evidence.confidence must be between 0 and 1")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "sourceType": self.source_type,
            "locator": self.locator,
            "asOf": self.as_of,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ValuePool:
    """One quantified, governable opportunity or cash-release pool."""

    pool_id: str
    name: str
    domain: str
    value_amount: float
    value_currency: str
    value_basis: str
    confidence: float
    owner: str
    evidence: List[EvidenceReference] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    release_gates: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.pool_id:
            errors.append("pool_id is required")
        if not self.name:
            errors.append("name is required")
        if self.domain not in ALLOWED_DOMAINS:
            errors.append(f"domain must be one of {sorted(ALLOWED_DOMAINS)}")
        if self.value_amount < 0:
            errors.append("value_amount must be non-negative")
        if not self.value_currency:
            errors.append("value_currency is required")
        if not self.value_basis:
            errors.append("value_basis is required")
        if not 0 <= self.confidence <= 1:
            errors.append("confidence must be between 0 and 1")
        if not self.owner:
            errors.append("owner is required")
        if not self.evidence:
            errors.append("at least one evidence reference is required")
        for evidence in self.evidence:
            errors.extend(evidence.validate())
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "poolId": self.pool_id,
            "name": self.name,
            "domain": self.domain,
            "valueAmount": self.value_amount,
            "valueCurrency": self.value_currency,
            "valueBasis": self.value_basis,
            "confidence": self.confidence,
            "owner": self.owner,
            "evidence": [item.to_dict() for item in self.evidence],
            "assumptions": list(self.assumptions),
            "risks": list(self.risks),
            "releaseGates": list(self.release_gates),
        }


@dataclass(frozen=True)
class GovernedValuePoolPacket:
    """Decision-packet fragment consumable by CFO and board workflows."""

    packet_id: str
    decision_statement: str
    recommendation: str
    value_pools: List[ValuePool]
    decision_owner: str
    decision_deadline: str = ""

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.packet_id:
            errors.append("packet_id is required")
        if not self.decision_statement:
            errors.append("decision_statement is required")
        if not self.recommendation:
            errors.append("recommendation is required")
        if not self.decision_owner:
            errors.append("decision_owner is required")
        if not self.value_pools:
            errors.append("at least one value pool is required")
        for pool in self.value_pools:
            errors.extend(f"{pool.pool_id}: {error}" for error in pool.validate())
        return errors

    def to_dict(self) -> Dict[str, Any]:
        errors = self.validate()
        if errors:
            raise ValueError("Invalid governed value-pool packet: " + "; ".join(errors))
        return {
            "packetId": self.packet_id,
            "decisionStatement": self.decision_statement,
            "recommendation": self.recommendation,
            "decisionOwner": self.decision_owner,
            "decisionDeadline": self.decision_deadline,
            "valuePools": [pool.to_dict() for pool in self.value_pools],
        }


def adapt_value_pool_packet(data: Mapping[str, Any]) -> GovernedValuePoolPacket:
    """Adapt a plain mapping into the governed packet contract.

    The adapter intentionally rejects missing evidence instead of turning an
    unsupported estimate into a board-ready claim.
    """
    pools = [
        ValuePool(
            pool_id=item["poolId"],
            name=item["name"],
            domain=item["domain"],
            value_amount=float(item["valueAmount"]),
            value_currency=item["valueCurrency"],
            value_basis=item["valueBasis"],
            confidence=float(item["confidence"]),
            owner=item["owner"],
            evidence=[
                EvidenceReference(
                    source_id=source["sourceId"],
                    source_type=source["sourceType"],
                    locator=source.get("locator", ""),
                    as_of=source.get("asOf", ""),
                    confidence=float(source.get("confidence", 0.0)),
                )
                for source in item.get("evidence", [])
            ],
            assumptions=list(item.get("assumptions", [])),
            risks=list(item.get("risks", [])),
            release_gates=list(item.get("releaseGates", [])),
        )
        for item in data.get("valuePools", [])
    ]
    packet = GovernedValuePoolPacket(
        packet_id=data["packetId"],
        decision_statement=data["decisionStatement"],
        recommendation=data["recommendation"],
        decision_owner=data["decisionOwner"],
        decision_deadline=data.get("decisionDeadline", ""),
        value_pools=pools,
    )
    errors = packet.validate()
    if errors:
        raise ValueError("Invalid governed value-pool packet: " + "; ".join(errors))
    return packet
