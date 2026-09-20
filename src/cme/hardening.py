"""CHP-hardened decision gate for board-ready CFO claims.

Port of the pattern proven in erp-control-plane (``api/genbi/chp.py``, commit
``70678cc``) onto meshcfo's domain: the consequential action is a CFO session
producing a board-ready artifact (ForecastPack / InvestmentCaseMemo /
BoardOutput), and every such claim must trace to the agent that produced it.

Four hardening stages wrap ``CFOOperatingSystem.run``:

1. **R0 gate — before the engine.** ``chp.gates.evaluate_r0_gate`` with
   CFO-brief-shaped criteria: the brief is *solvable* (title and problem are
   non-empty), *scoped* (the built dossier carries explicit scope), *valid*
   (task-specific input well-formedness), and *worth_it* (high-stakes by
   default). HALT refuses the session with nothing executed or persisted.
2. **Foundation pass — after the mesh orchestration.** The deterministic
   adversary scores the produced artifact out of 100: 40 for guardrail-clean
   orchestration (every agent turn completed without failure notes), 30 for a
   bounded, quantified artifact, and 30 for golden parity — the artifact's
   headline number matching the pinned ``evals/golden_qa.json`` case for this
   brief. meshcfo's domain is board-ready financial claims, so every domain
   maps onto CHP's floor-100 domains (``finance`` / ``capital_allocation`` /
   ``board_decision``): without parity evidence the foundation cannot
   self-certify, and the claim needs a named human confirmer. A parity
   *mismatch* is fatal — a claim contradicting pinned truth must not be
   issued, and no confirmer can wave it through.
3. **Human lock.** The hardened case starts ``EXPLORING`` and is explicitly
   transitioned to ``PROVISIONAL_LOCK``; a named confirmer
   (``confirmed_by``) locks it (``LOCKED``) through CHP third-party
   validation. ``MESH_CFO_CHP_REQUIRE_HUMAN_LOCK`` (default on) makes that
   confirmation mandatory for every session.
4. **Decision record.** The case, verdicts, parity evidence, and the rendered
   artifact are sealed into a CHP payload envelope and appended to the
   decision ledger (append-only JSONL). CHP's ``validate_payload_envelope``
   is STRUCTURE-ONLY, not content integrity, so the ledger stores its own
   SHA-256 digest over the sealed body and revalidates both on read — a
   tampered record reads as ``integrity_valid: false``.

The session protocol itself (foundation disclosure, devil's-advocate rounds,
registry) remains meshcfo's vendored ``cme.chp``; this gate hardens the
decision points on top of it using the canonical ``chp`` distribution.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Mapping

from chp import (
    CHPOrchestrator,
    CHPReport,
    DecisionCase,
    Dossier,
    FoundationAttack,
    FoundationDisclosure,
    SessionStatus,
    ThirdPartyValidation,
    ValidationResult,
    Verdict,
    apply_third_party_validation,
    build_payload_envelope,
    validate_payload_envelope,
)
from chp.foundation import foundation_floor
from chp.gates import GateEvaluation, evaluate_r0_gate

logger = logging.getLogger(__name__)

# Deterministic adversary scoring (out of 100). Every meshcfo domain gates at
# CHP's finance floor of exactly 100, so only a parity-verified claim can
# self-certify a board-ready number.
_GUARDRAIL_POINTS = 40
_BOUNDED_RESULT_POINTS = 30
_PARITY_POINTS = 30
_FULL_SCORE = _GUARDRAIL_POINTS + _BOUNDED_RESULT_POINTS + _PARITY_POINTS

# meshcfo task types -> canonical CHP floor-100 domains. The vendored session
# calls the forecast domain "forecast"; the gate renames it to CHP's
# "finance" so the floor map applies exactly (unlisted domains would fall
# back to the general floor of 70).
_CANONICAL_DOMAINS: Mapping[str, str] = {
    "forecast": "finance",
    "capital_allocation": "capital_allocation",
    "board_decision": "board_decision",
}
_GENERAL_DOMAIN = "general"

DEFAULT_DECISIONS_PATH = Path(".meshcfo") / "chp_decisions.jsonl"
DEFAULT_GOLDEN_PATH = Path("evals") / "golden_qa.json"
REQUIRE_HUMAN_LOCK_ENV = "MESH_CFO_CHP_REQUIRE_HUMAN_LOCK"
DECISIONS_PATH_ENV = "MESH_CFO_CHP_DECISIONS_PATH"
GOLDEN_PATH_ENV = "MESH_CFO_CHP_GOLDEN_PATH"

# Artifact label per golden metric: the comparable scalar is read from the
# RENDERED board-ready artifact, not from the brief, so rendering drift is
# what parity catches.
_METRIC_LABELS: Mapping[str, str] = {
    "investment_amount_usd": "Amount:",
    "base_revenue_usd": "Base revenue:",
    "base_opex_usd": "Base opex:",
}
_MONEY_RE = re.compile(r"\$(-?[\d,]+(?:\.\d+)?)")
_OPTION_RE = re.compile(r"^- (?:\(recommended\) )?\d+\. ", re.MULTILINE)


class ChpRejection(Exception):
    """CHP refused the session (R0 HALT, foundation REFRAME, or lock required)."""

    def __init__(self, reason: str, evaluation: GateEvaluation | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evaluation = evaluation


@dataclass(frozen=True)
class ChpGateSettings:
    """Where the gate reads pinned truth and writes durable decisions."""

    decisions_path: Path = DEFAULT_DECISIONS_PATH
    golden_path: Path = DEFAULT_GOLDEN_PATH
    require_human_lock: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ChpGateSettings":
        source = os.environ if env is None else env
        require_raw = source.get(REQUIRE_HUMAN_LOCK_ENV, "1").strip().lower()
        require_lock = require_raw not in {"0", "false", "no", "off"}
        return cls(
            decisions_path=Path(source.get(DECISIONS_PATH_ENV, str(DEFAULT_DECISIONS_PATH))),
            golden_path=Path(source.get(GOLDEN_PATH_ENV, str(DEFAULT_GOLDEN_PATH))),
            require_human_lock=require_lock,
        )


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def load_golden(path: Path | str) -> dict[str, Any] | None:
    """Load the pinned golden QA set; unusable sets disable parity evidence.

    Golden parsers signal an unusable set with exceptions (ERP's loader even
    used SystemExit, CLI-friendly) — the gate treats every failure here as
    parity-unavailable, never as a session blocker on its own.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, SystemExit) as exc:
        logger.warning("golden QA set unavailable (%s) — parity evidence disabled", exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        logger.warning("golden QA set at %s has no cases — parity evidence disabled", path)
        return None
    return data


def within_tolerance(expected: float, tolerance: float, actual: float) -> bool:
    """Relative-tolerance comparison; zero expected values compare exactly."""
    if expected == 0:
        return actual == 0
    return abs(actual - expected) <= tolerance * abs(expected)


def _extract_labeled_number(rendered: str, label: str) -> float | None:
    """The dollar figure rendered after a known artifact label, if any."""
    match = re.search(re.escape(label) + r"\s*" + _MONEY_RE.pattern, rendered)
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_options_count(rendered: str) -> float | None:
    """How many numbered option bullets the board packet rendered."""
    count = len(_OPTION_RE.findall(rendered))
    return float(count) if count else None


def _extract_metric(rendered: str, golden_case: Mapping[str, Any]) -> float | None:
    metric = str(golden_case.get("metric", ""))
    if metric == "board_options_count":
        return _extract_options_count(rendered)
    label = _METRIC_LABELS.get(metric)
    if label is None:
        return None
    return _extract_labeled_number(rendered, label)


def _brief_is_valid(brief: Any) -> bool:
    """Task-specific input well-formedness (R0's Valid criterion)."""
    name = type(brief).__name__
    if name == "ForecastBrief":
        return (
            brief.base_revenue_usd >= 0
            and brief.base_opex_usd >= 0
            and 0 <= brief.churn_assumption_pct < 1
            and brief.growth_assumption_pct > -1
        )
    if name == "InvestmentBrief":
        return brief.investment_amount_usd > 0 and brief.expected_payback_months > 0
    if name == "BoardBrief":
        options = brief.options or []
        return len(options) == 0 or len(options) >= 2  # empty falls back to the standard packet
    return True


@dataclass(frozen=True)
class ParityEvidence:
    """The rendered claim vs the pinned golden QA case (when one matches)."""

    case_id: str
    metric: str
    unit: str
    expected: float
    tolerance: float
    actual: float | None  # None = the artifact is not a comparable scalar
    within_tolerance: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FoundationAssessment:
    """The deterministic adversary's verdict on a produced artifact."""

    score: int
    domain: str
    findings: List[str] = field(default_factory=list)
    parity: ParityEvidence | None = None
    golden_matched: bool = False


@dataclass(frozen=True)
class ChpDecision:
    """A hardened CFO session: the CHP case, its report, and the assessment."""

    case: DecisionCase
    report: CHPReport
    assessment: FoundationAssessment


class DecisionLedger:
    """Append-only JSONL of CHP decision records; integrity re-checked on read."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _read_all(self) -> List[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def list(self, limit: int = 100) -> List[dict[str, Any]]:
        """Newest-first records with envelope and body integrity re-validated."""
        return [self._checked(entry) for entry in self._read_all()[-limit:]][::-1]

    def get(self, decision_id: str) -> dict[str, Any] | None:
        """Newest record for a decision id (later lock events shadow earlier ones)."""
        for entry in reversed(self._read_all()):
            if entry.get("decision_id") == decision_id:
                return self._checked(entry)
        return None

    @staticmethod
    def _checked(entry: dict[str, Any]) -> dict[str, Any]:
        """Re-validate a record on read: envelope structure and body digest.

        The CHP payload envelope validates structure only, so the ledger adds
        its own SHA-256 digest over the sealed body — a tampered record reads
        as ``integrity_valid: false``.
        """
        body = entry.get("body", "")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return {
            **entry,
            "envelope_valid": validate_payload_envelope(entry.get("envelope", "")),
            "integrity_valid": digest == entry.get("body_sha256"),
        }


class ChpDecisionGate:
    """Runs a CFO session through CHP: R0 -> foundation -> human lock -> record."""

    def __init__(self, settings: ChpGateSettings | None = None) -> None:
        self.settings = settings or ChpGateSettings.from_env()
        self.records = DecisionLedger(self.settings.decisions_path)

    # ------------------------------------------------------------- golden set
    def _match_golden(self, brief: Any) -> dict[str, Any] | None:
        golden = load_golden(self.settings.golden_path)
        if not golden:
            return None
        normalized = _normalize_key(brief.title)
        for case in golden["cases"]:
            if (
                case.get("task_type") == brief.task_type.value
                and _normalize_key(str(case.get("title", ""))) == normalized
            ):
                return case
        return None

    # ------------------------------------------------------------------- R0
    def open_r0(self, brief: Any, case: Any) -> GateEvaluation:
        """The pre-execution gate: HALT before any agent sees the brief."""
        evaluation = evaluate_r0_gate(
            solvable=bool(brief.title.strip()) and bool(brief.problem.strip()),
            scoped=bool(case.dossier and case.dossier.scope),
            valid=_brief_is_valid(brief),
            worth_it=bool(brief.high_stakes),
        )
        if evaluation.verdict != Verdict.PASS:
            failed = sorted(name for name, result in evaluation.results.items() if result != "PASS")
            raise ChpRejection(
                "CHP R0 gate: the CFO session failed " + ", ".join(failed),
                evaluation,
            )
        return evaluation

    # ------------------------------------------------------------ foundation
    def assess_foundation(self, brief: Any, artifact: Any, orchestration: Any) -> FoundationAssessment:
        """The deterministic adversary scores the produced artifact (0-100)."""
        findings: List[str] = []
        score = 0

        turns = list(orchestration.turns)
        failure_notes = [
            note for turn in turns for note in turn.handoff_notes if note.startswith("warning:")
        ]
        if turns and not failure_notes:
            score += _GUARDRAIL_POINTS
            findings.append(
                "orchestration guardrails passed: "
                f"{len(turns)} agent turns completed with per-claim provenance and no failure notes"
            )
        else:
            findings.append("orchestration guardrail failure: agent turns incomplete or warning notes present")

        rendered = artifact.render()
        if artifact.sections and any(character.isdigit() for character in rendered):
            score += _BOUNDED_RESULT_POINTS
            findings.append(
                f"bounded result: {len(artifact.sections)} sections carrying quantified board-ready claims"
            )
        else:
            findings.append("artifact carries no quantified content — no result evidence")

        golden_case = self._match_golden(brief)
        parity: ParityEvidence | None = None
        if golden_case is None:
            findings.append("no golden QA case matches this brief — parity evidence unavailable")
        else:
            actual = _extract_metric(rendered, golden_case)
            if actual is None:
                findings.append(
                    "golden case matched but the artifact does not expose a comparable scalar"
                    " — parity evidence unavailable"
                )
            else:
                expected = float(golden_case["expected"])
                tolerance = float(golden_case.get("tolerance", 0.0))
                matched = within_tolerance(expected, tolerance, actual)
                parity = ParityEvidence(
                    case_id=str(golden_case.get("id", "")),
                    metric=str(golden_case.get("metric", "")),
                    unit=str(golden_case.get("unit", "")),
                    expected=expected,
                    tolerance=tolerance,
                    actual=actual,
                    within_tolerance=matched,
                )
                if matched:
                    score += _PARITY_POINTS
                    findings.append(
                        f"golden parity: {parity.case_id} ({parity.metric}) expected"
                        f" {parity.expected} ± {parity.tolerance} {parity.unit}, got {parity.actual}"
                    )
                else:
                    findings.append(
                        f"golden parity MISMATCH: {parity.case_id} ({parity.metric}) expected"
                        f" {parity.expected} ± {parity.tolerance} {parity.unit}, got {parity.actual}"
                    )

        domain = _CANONICAL_DOMAINS.get(vendored_domain(brief), _GENERAL_DOMAIN)
        return FoundationAssessment(
            score=min(score, _FULL_SCORE),
            domain=domain,
            findings=findings,
            parity=parity,
            golden_matched=golden_case is not None,
        )

    # --------------------------------------------------------------- session
    def harden(
        self,
        *,
        brief: Any,
        case: Any,
        disclosure: Any,
        artifact: Any,
        orchestration: Any,
    ) -> ChpDecision:
        """Run the CHP foundation pass and open the case as PROVISIONAL_LOCK."""
        assessment = self.assess_foundation(brief, artifact, orchestration)
        if assessment.parity is not None and assessment.parity.within_tolerance is False:
            raise ChpRejection(
                f"CHP foundation: {assessment.findings[-1]} — a board-ready claim"
                " contradicting the pinned golden QA set must not be issued; fix the"
                " driver inputs or regenerate the golden set."
            )

        chp_case = DecisionCase(
            decision_id=case.decision_id,
            title=case.title,
            domain=assessment.domain,
            created_at=case.created_at or _utcnow_iso(),
            owner=case.owner,
            high_stakes=case.high_stakes,
            origin_system=case.origin_system,
            origin_model=case.origin_model,
            partner_system=case.partner_system,
            partner_model=case.partner_model,
            dossier=Dossier.from_dict(case.dossier.to_dict()) if case.dossier else None,
        )
        pip_disclosure = FoundationDisclosure(
            weakest_assumptions=list(disclosure.weakest_assumptions),
            invalidation_conditions=list(disclosure.invalidation_conditions),
            key_vulnerability=disclosure.key_vulnerability
            or "the claim rests on brief assumptions not yet evidenced by actuals",
        )
        parity = assessment.parity
        attack = FoundationAttack(
            attack_summary="; ".join(assessment.findings),
            foundation_score=assessment.score,
            vulnerability_strike=(
                f"single-source parity: the claim is pinned only to golden case"
                f" {parity.case_id} ({parity.metric})"
                if parity
                else "without golden parity the board-ready claim rests only on clean"
                " orchestration, not on pinned ground truth"
            ),
            assumption_attacks=[
                "parity-check the artifact's headline number against the pinned golden QA baseline",
                "re-derive the claim from the brief inputs — agent drift breaks parity",
                "confirm the orchestration stayed guardrail-clean with per-claim provenance",
            ],
            invalidation_exploitation=[
                "if the artifact's headline number drifts from pinned truth, the claim is fatal",
                "if any agent turn emits a failure note, the foundation loses its guardrail points",
            ],
        )

        # Fresh orchestrator per case: the protocol registry is in-memory state
        # we do not rely on — the decision ledger is the durable record.
        report = CHPOrchestrator().run_initial_session(
            case=chp_case, foundation_disclosure=pip_disclosure, foundation_attack=attack
        )

        # The gate collapses CHP's multi-round phase flow into one session
        # step: the case opens EXPLORING and is explicitly transitioned to
        # PROVISIONAL_LOCK — every hardened claim is a provisional decision
        # pending human confirmation (which apply_third_party_validation
        # locks). A REFRAME verdict keeps that status too — the claim may
        # only proceed through the same human lock, never self-certify.
        chp_case.status = SessionStatus.PROVISIONAL_LOCK
        return ChpDecision(chp_case, report, assessment)

    # ------------------------------------------------------------- human lock
    def enforce_lock_policy(self, decision: ChpDecision, confirmed_by: str | None) -> None:
        """Refuse sessions whose foundation verdict or lock policy fails.

        A verdict below PASS is fatal on its own — a named confirmer cannot
        cure a score under the domain floor (same rule as parity mismatch).
        """
        if decision.report.foundation_verdict != Verdict.PASS:
            raise ChpRejection(
                f"CHP foundation: {decision.report.foundation_verdict.value}"
                f" (score {decision.case.foundation_score}, {decision.assessment.domain}"
                f" domain, floor {foundation_floor(decision.assessment.domain)})"
                " — the board-ready claim is below its domain floor; fix the"
                " driver inputs or regenerate the golden baseline deliberately."
                " confirmed_by cannot cure a floor breach."
            )
        if self.settings.require_human_lock and not confirmed_by:
            raise ChpRejection(
                f"CHP human lock: {REQUIRE_HUMAN_LOCK_ENV} is on (default) — every"
                " board-ready claim needs a named confirmer (confirmed_by)."
            )

    def lock(self, decision: ChpDecision, confirmed_by: str) -> SessionStatus:
        """Third-party confirmation: PROVISIONAL_LOCK -> LOCKED (recorded in the case)."""
        return apply_third_party_validation(
            decision.case,
            ThirdPartyValidation(
                validator=confirmed_by,
                item=decision.case.decision_id,
                challenge="Confirm the board-ready claim is grounded and stress-tested",
                result=ValidationResult.CONFIRM,
                rationale="Named confirmer approved the CFO session via the meshcfo interface",
            ),
        )

    # ----------------------------------------------------------------- record
    def record(
        self,
        decision: ChpDecision,
        *,
        brief: Any,
        artifact: Any,
        orchestration: Any,
        confirmed_by: str | None,
    ) -> dict[str, Any]:
        """Seal the decision into a CHP payload envelope and append the ledger."""
        case = decision.case
        body = json.dumps(
            {
                "decision_id": case.decision_id,
                "title": case.title,
                "domain": case.domain,
                "task_type": brief.task_type.value,
                "r0_verdict": decision.report.r0_verdict.value,
                "foundation_verdict": decision.report.foundation_verdict.value,
                "foundation_score": case.foundation_score,
                "adversary_findings": decision.assessment.findings,
                "parity": decision.assessment.parity.to_dict()
                if decision.assessment.parity
                else None,
                "artifact_markdown": artifact.render(),
                "agents": sorted({turn.agent for turn in orchestration.turns}),
                "locked_decisions": list(case.locked_decisions),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        envelope = build_payload_envelope(body, route="CFO_SESSION")
        entry = {
            "decision_id": case.decision_id,
            "created_at": case.created_at,
            "title": case.title,
            "domain": case.domain,
            "task_type": brief.task_type.value,
            "session_status": case.status.value,
            "r0_verdict": decision.report.r0_verdict.value,
            "foundation_verdict": decision.report.foundation_verdict.value,
            "foundation_score": case.foundation_score,
            "confirmed_by": confirmed_by,
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "envelope": envelope.render(),
        }
        self.records.append(entry)
        return entry

    def record_lock(
        self,
        *,
        decision_id: str,
        session_status: str,
        confirmed_by: str | None,
        rationale: str,
    ) -> dict[str, Any]:
        """Append the post-run lock event (a later confirmation or rejection)."""
        body = json.dumps(
            {
                "decision_id": decision_id,
                "event": "third_party_lock",
                "session_status": session_status,
                "confirmed_by": confirmed_by,
                "rationale": rationale,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        envelope = build_payload_envelope(body, route="CFO_LOCK")
        entry = {
            "decision_id": decision_id,
            "created_at": _utcnow_iso(),
            "session_status": session_status,
            "confirmed_by": confirmed_by,
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "envelope": envelope.render(),
        }
        self.records.append(entry)
        return entry


def vendored_domain(brief: Any) -> str:
    """The vendored session domain for a brief (before canonical renaming).

    Mirrors ``cme.cfo_os.dossier_builders._domain_for`` without importing it.
    """
    task_value = getattr(brief.task_type, "value", "")
    return {
        "forecast": "forecast",
        "investment_case": "capital_allocation",
        "board_output": "board_decision",
    }.get(task_value, _GENERAL_DOMAIN)
