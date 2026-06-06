"""SpacetimeDB Python SDK client for Multi-Agent CFO OS.

Connects to the SpacetimeDB backend module via HTTP API, wrapping
call/reducer patterns in a clean Python interface. Designed to be
imported by the existing ``cme`` package.

Usage::

    from cme.spacetime_client import SpacetimeClient

    client = SpacetimeClient(host="http://localhost:3000", db_name="cfo_os")
    brief_id = client.submit_brief(
        title="FY2027 Revenue Forecast",
        company="AcmeCorp",
        problem="Forecast revenue under three scenarios",
        task_type="Forecast",
    )
    artifacts = client.get_artifacts(brief_id)
    for a in artifacts:
        print(a["rendered_markdown"][:200])
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, List, Optional

import requests

logger = logging.getLogger("cfo_os.spacetime")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SpacetimeError(Exception):
    """Base exception for SpacetimeDB client errors."""


class ReducerError(SpacetimeError):
    """The remote reducer returned an error."""


class ConnectionError_Spacetime(SpacetimeError):
    """Could not reach the SpacetimeDB gateway."""


# ---------------------------------------------------------------------------
# Data classes mirroring the SpacetimeDB tables
# ---------------------------------------------------------------------------


@dataclass
class Brief:
    brief_id: str = ""
    title: str = ""
    company: str = ""
    problem: str = ""
    task_type: str = "Forecast"  # Forecast | InvestmentCase | BoardOutput
    priority: int = 1
    constraints: str = ""
    created_at: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict) -> "Brief":
        return cls(**{k: row.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class AgentTurnRecord:
    turn_id: str = ""
    brief_id: str = ""
    agent_name: str = "Finance"
    status: str = "InProgress"
    expansion_text: str = ""
    compression_text: str = ""
    confidence: float = 0.0
    created_at: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "AgentTurnRecord":
        return cls(**{k: row.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class DecisionCase:
    decision_id: str = ""
    brief_id: str = ""
    phase: int = 0
    round_num: int = 0
    status: str = "Exploring"
    foundation_score: Optional[int] = None
    adversary_score: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> "DecisionCase":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class AuditEntry:
    audit_id: str = ""
    decision_id: str = ""
    producer: str = ""
    claim: str = ""
    grounding: str = ""
    confidence: float = 0.0
    chp_finding: str = ""
    created_at: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "AuditEntry":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class FinalArtifact:
    artifact_id: str = ""
    brief_id: str = ""
    task_type: str = ""
    rendered_markdown: str = ""
    total_time_ms: int = 0
    version: int = 1

    @classmethod
    def from_row(cls, row: dict) -> "FinalArtifact":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class SharedContextEntity:
    entity_id: str = ""
    brief_id: str = ""
    entity_type: str = "Entity"
    name: str = ""
    properties: str = "{}"
    semantic_tags: str = "[]"

    @classmethod
    def from_row(cls, row: dict) -> "SharedContextEntity":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SpacetimeDB HTTP client
# ---------------------------------------------------------------------------


class SpacetimeClient:
    """HTTP client for the CFO OS SpacetimeDB module.

    Wraps ``POST /v1/database/:db/reduce`` (to call reducers)
    and ``GET /v1/database/:db/scan/:table`` (to query tables).

    Attributes
    ----------
    host : str
        SpacetimeDB gateway URL (e.g. ``http://localhost:3000``).
    db_name : str
        Target database name, default ``cfo_os``.
    """

    def __init__(
        self,
        host: str = "http://localhost:3000",
        db_name: str = "cfo_os",
        timeout: int = 30,
    ):
        self.host = host.rstrip("/")
        self.db_name = db_name
        self._session = requests.Session()
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, *parts: str) -> str:
        return f"{self.host}/{'/'.join(p.strip('/') for p in parts)}"

    def _reduce(self, reducer_name: str, args: List[Any]) -> dict:
        """Call a SpacetimeDB reducer via POST /v1/database/:db/reduce."""
        payload = json.dumps({"fn": reducer_name, "args": args})
        url = self._url("v1", "database", self.db_name, "reduce")
        logger.debug("POST %s body=%s", url, payload[:500])

        try:
            resp = self._session.post(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ConnectionError_Spacetime(
                f"Cannot reach SpacetimeDB at {url}: {exc}"
            ) from exc

        if resp.status_code not in (200, 204):
            raise ReducerError(
                f"Reducer '{reducer_name}' failed (HTTP {resp.status_code}): "
                f"{resp.text[:500]}"
            )

        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return {"raw": resp.text}

    def _scan(self, table: str) -> List[dict]:
        """Scan a table via GET /v1/database/:db/scan/:table."""
        url = self._url("v1", "database", self.db_name, "scan", table)
        logger.debug("GET %s", url)

        try:
            resp = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ConnectionError_Spacetime(
                f"Cannot reach SpacetimeDB at {url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise SpacetimeError(
                f"Scan '{table}' failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )

        return resp.json()

    # ------------------------------------------------------------------
    # Reducer wrappers
    # ------------------------------------------------------------------

    def submit_brief(
        self,
        title: str,
        company: str,
        problem: str,
        task_type: str = "Forecast",
        priority: int = 1,
        constraints: str = "",
        brief_id: Optional[str] = None,
    ) -> str:
        """Submit a new CFO analysis brief.

        Returns the *brief_id* generated server-side (or the one provided).
        """
        bid = brief_id or f"b-{uuid.uuid4().hex[:12]}"
        self._reduce("submit_brief", [bid, title, company, problem, task_type, priority, constraints])
        logger.info("Brief submitted: %s", bid)
        return bid

    def record_turn(
        self,
        brief_id: str,
        agent_name: str,
        status: str = "Complete",
        expansion_text: str = "",
        compression_text: str = "",
        confidence: float = 0.0,
    ) -> str:
        """Record an agent reasoning cycle turn.

        Returns the generated *turn_id*.
        """
        turn_id = f"t-{uuid.uuid4().hex[:12]}"
        self._reduce(
            "record_agent_turn",
            [turn_id, brief_id, agent_name, status, expansion_text, compression_text, confidence],
        )
        logger.info("Turn %s recorded for brief %s", turn_id, brief_id)
        return turn_id

    def record_agent_turn(
        self,
        brief_id: str,
        agent_name: str,
        status: str = "Complete",
        expansion_text: str = "",
        compression_text: str = "",
        confidence: float = 0.0,
    ) -> str:
        """Alias for :meth:`record_turn`."""
        return self.record_turn(
            brief_id, agent_name, status, expansion_text, compression_text, confidence
        )

    def update_decision_state(
        self,
        decision_id: str,
        new_status: str,
        foundation_score: Optional[int] = None,
        adversary_score: Optional[int] = None,
    ) -> None:
        """Advance a CHP-hardened decision state machine."""
        self._reduce(
            "update_decision_state",
            [decision_id, new_status, foundation_score, adversary_score],
        )
        logger.info("Decision %s → %s (foundation=%s)", decision_id, new_status, foundation_score)

    def publish_artifact(
        self,
        brief_id: str,
        rendered_markdown: str,
        version: int = 1,
        task_type: str = "Forecast",
        total_time_ms: int = 0,
    ) -> str:
        """Publish a final rendered artifact.

        Returns the generated *artifact_id*.
        """
        artifact_id = f"a-{uuid.uuid4().hex[:12]}"
        self._reduce(
            "publish_artifact",
            [artifact_id, brief_id, task_type, rendered_markdown, total_time_ms, version],
        )
        logger.info("Artifact %s published for brief %s", artifact_id, brief_id)
        return artifact_id

    def seed_briefs(self) -> None:
        """Bulk-insert sample briefs for development / testing."""
        self._reduce("seed_briefs", [])
        logger.info("Seeded sample briefs")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_artifacts(self, brief_id: str) -> List[dict]:
        """Return all FinalArtifact rows for a given *brief_id*."""
        rows = self._scan("final_artifact")
        return [r for r in rows if r.get("brief_id") == brief_id]

    def get_briefs(self) -> List[dict]:
        """Return all Brief rows (raw dicts)."""
        return self._scan("brief")

    def get_brief(self, brief_id: str) -> Optional[dict]:
        """Return a single Brief row by ID, or None."""
        rows = self._scan("brief")
        for r in rows:
            if r.get("brief_id") == brief_id:
                return r
        return None

    def get_decision_cases(self, brief_id: Optional[str] = None) -> List[dict]:
        """Return DecisionCase rows, optionally filtered by brief_id."""
        rows = self._scan("decision_case")
        if brief_id:
            return [r for r in rows if r.get("brief_id") == brief_id]
        return rows

    def get_audit_entries(self, decision_id: Optional[str] = None) -> List[dict]:
        """Return AuditEntry rows, optionally filtered by decision_id."""
        rows = self._scan("audit_entry")
        if decision_id:
            return [r for r in rows if r.get("decision_id") == decision_id]
        return rows

    def get_agent_turns(self, brief_id: Optional[str] = None) -> List[dict]:
        """Return AgentTurnRecord rows, optionally filtered by brief_id."""
        rows = self._scan("agent_turn_record")
        if brief_id:
            return [r for r in rows if r.get("brief_id") == brief_id]
        return rows

    def get_context_entities(self, brief_id: Optional[str] = None) -> List[dict]:
        """Return SharedContextEntity rows, optionally filtered by brief_id."""
        rows = self._scan("shared_context_entity")
        if brief_id:
            return [r for r in rows if r.get("brief_id") == brief_id]
        return rows

    # ------------------------------------------------------------------
    # Live subscription (SSE)
    # ------------------------------------------------------------------

    def subscribe_audit(
        self,
        decision_id: str,
        callback: Callable[[AuditEntry], None],
        poll_interval_ms: int = 2000,
    ) -> None:
        """Long-poll subscription for audit entries on a decision.

        This is a blocking loop that polls the ``audit_entry`` table every
        *poll_interval_ms* milliseconds. For production use, wrap in a
        background thread or use SpacetimeDB's native SSE subscription.

        Parameters
        ----------
        decision_id : str
            Filter audit entries to this decision.
        callback : Callable[[AuditEntry], None]
            Invoked for each new audit row not seen before.
        poll_interval_ms : int
            Milliseconds between polls (default 2000).
        """
        seen: set[str] = set()
        logger.info("Starting audit subscription for decision %s", decision_id)

        while True:
            try:
                rows = self._scan("audit_entry")
            except SpacetimeError:
                logger.warning("Audit poll failed — retrying in %dms", poll_interval_ms)
                time.sleep(poll_interval_ms / 1000.0)
                continue

            for row in rows:
                aid = row.get("audit_id", "")
                if aid and aid not in seen and row.get("decision_id") == decision_id:
                    seen.add(aid)
                    entry = AuditEntry.from_row(row)
                    try:
                        callback(entry)
                    except Exception:
                        logger.exception("Audit callback raised")

            time.sleep(poll_interval_ms / 1000.0)

    # ------------------------------------------------------------------
    # Migration bridge from CockroachDB
    # ------------------------------------------------------------------

    def migrate_from_cockroach(self, dump_path: str) -> int:
        """Bulk-migrate from a CockroachDB JSON dump into SpacetimeDB.

        Expects a JSON file (one object per line, or a JSON array of
        objects), where each object has fields matching the SpacetimeDB
        ``Brief`` table schema.

        Parameters
        ----------
        dump_path : str
            Path to the JSON dump file.

        Returns
        -------
        int
            Number of briefs migrated.
        """
        import os

        if not os.path.isfile(dump_path):
            raise FileNotFoundError(f"Dump file not found: {dump_path}")

        with open(dump_path) as fh:
            content = fh.read().strip()

        if content.startswith("["):
            records: List[dict] = json.loads(content)
        else:
            records = [json.loads(line) for line in content.splitlines() if line.strip()]

        migrated = 0
        for rec in records:
            bid = rec.get("brief_id") or f"b-mig-{uuid.uuid4().hex[:12]}"
            try:
                self._reduce(
                    "submit_brief",
                    [
                        bid,
                        rec.get("title", ""),
                        rec.get("company", ""),
                        rec.get("problem", ""),
                        rec.get("task_type", "Forecast"),
                        int(rec.get("priority", 1)),
                        str(rec.get("constraints", "")),
                    ],
                )
                migrated += 1
            except SpacetimeError as exc:
                logger.warning("Skipping row %s: %s", bid, exc)

        logger.info("Migrated %d briefs from %s", migrated, dump_path)
        return migrated


# ---------------------------------------------------------------------------
# Health check helper
# ---------------------------------------------------------------------------


def health_check(host: str = "http://localhost:3000", db_name: str = "cfo_os") -> dict:
    """Quick connectivity test — scans ``brief`` table."""
    try:
        client = SpacetimeClient(host, db_name)
        briefs = client.get_briefs()
        return {
            "status": "ok",
            "host": host,
            "database": db_name,
            "brief_count": len(briefs),
            "backend": "SpacetimeDB",
        }
    except SpacetimeError as exc:
        return {"status": "error", "error": str(exc), "host": host, "database": db_name}
    except Exception as exc:
        return {"status": "error", "error": f"Unexpected: {exc}", "host": host, "database": db_name}


# ---------------------------------------------------------------------------
# CLI entry point (quick test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client = SpacetimeClient()

    if len(sys.argv) > 1 and sys.argv[1] == "health":
        print(json.dumps(health_check(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "seed":
        client.seed_briefs()
        print("Seeded.")
    else:
        print(
            "Usage: python client.py [health|seed]\n"
            "Make sure SpacetimeDB is running at http://localhost:3000\n"
            "with database 'cfo_os' published."
        )
