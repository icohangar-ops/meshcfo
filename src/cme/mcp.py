"""Minimal stdio MCP server for MeshCFO."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cme.audit import AuditLedger
from cme.cfo_os import BoardBrief, CFOOperatingSystem, ForecastBrief, InvestmentBrief
from cme.chp import DecisionRegistry
from cme.context import ContextEngine
from cme.hardening import ChpGateSettings, DecisionLedger


JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
ToolHandler = Callable[[dict[str, Any]], JsonValue]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


class MeshCFOState:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path
        self.registry = DecisionRegistry.load(registry_path) if registry_path.exists() else DecisionRegistry()
        self.ledger = AuditLedger()
        # CHP decision ledger (append-only JSONL, integrity re-checked on read).
        self.decisions = DecisionLedger(ChpGateSettings.from_env().decisions_path)

    def save(self) -> None:
        self.registry.save(self.registry_path)


class StdioServer:
    def __init__(self, name: str, version: str, tools: list[ToolSpec]) -> None:
        self.name = name
        self.version = version
        self.tools = {tool.name: tool for tool in tools}

    def serve(self) -> None:
        while True:
            message = _read_message()
            if message is None:
                return
            method = message.get("method")
            if method == "initialize":
                _respond(message, {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "serverInfo": {"name": self.name, "version": self.version},
                    "capabilities": {"tools": {"listChanged": False}},
                })
                continue
            if method == "tools/list":
                _respond(message, {"tools": [self._tool_entry(tool) for tool in self.tools.values()]})
                continue
            if method == "tools/call":
                self._call_tool(message)
                continue
            if method in {"shutdown", "exit"}:
                if message.get("id") is not None:
                    _respond(message, {})
                return

    def _tool_entry(self, tool: ToolSpec) -> dict[str, Any]:
        return {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}

    def _call_tool(self, message: dict[str, Any]) -> None:
        params = message.get("params", {})
        name = params.get("name")
        tool = self.tools.get(name)
        if tool is None:
            _error(message, -32602, f"unknown tool: {name}")
            return
        arguments = params.get("arguments") or {}
        try:
            result = tool.handler(arguments)
        except Exception as exc:  # pragma: no cover - surfaced to caller
            _error(message, -32000, str(exc))
            return
        _respond(message, {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]})


def serve(registry_path: str = ".chp_registry.json") -> None:
    state = MeshCFOState(Path(registry_path))
    server = StdioServer(
        name="meshcfo",
        version="0.1.0",
        tools=[
            ToolSpec(
                name="forecast",
                description="Run a driver-based CFO forecast and return the board-ready artifact.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "problem": {"type": "string"},
                        "base_revenue_usd": {"type": "number"},
                        "base_opex_usd": {"type": "number"},
                        "growth_assumption_pct": {"type": "number"},
                        "churn_assumption_pct": {"type": "number"},
                        "confirmed_by": {"type": "string"},
                    },
                    "required": ["title", "company", "problem"],
                },
                handler=lambda args: _run_session(state, ForecastBrief(
                    title=args["title"],
                    company=args["company"],
                    problem=args["problem"],
                    base_revenue_usd=float(args.get("base_revenue_usd", 0.0)),
                    base_opex_usd=float(args.get("base_opex_usd", 0.0)),
                    growth_assumption_pct=float(args.get("growth_assumption_pct", 0.2)),
                    churn_assumption_pct=float(args.get("churn_assumption_pct", 0.08)),
                    strategic_priorities=list(args.get("strategic_priorities", [])),
                    constraints=list(args.get("constraints", [])),
                ), confirmed_by=args.get("confirmed_by")),
            ),
            ToolSpec(
                name="investment_case",
                description="Run a capital allocation case and return the CFO memo.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "problem": {"type": "string"},
                        "investment_amount_usd": {"type": "number"},
                        "expected_payback_months": {"type": "integer"},
                        "current_runway_months": {"type": "integer"},
                        "minimum_runway_months": {"type": "integer"},
                        "expected_upside": {"type": "array", "items": {"type": "string"}},
                        "key_risks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "company", "problem", "investment_amount_usd", "expected_payback_months"],
                },
                handler=lambda args: _run_session(state, InvestmentBrief(
                    title=args["title"],
                    company=args["company"],
                    problem=args["problem"],
                    investment_amount_usd=float(args["investment_amount_usd"]),
                    expected_payback_months=int(args.get("expected_payback_months", 18)),
                    current_runway_months=int(args.get("current_runway_months", 18)),
                    minimum_runway_months=int(args.get("minimum_runway_months", 12)),
                    expected_upside=list(args.get("expected_upside", [])),
                    key_risks=list(args.get("key_risks", [])),
                    strategic_priorities=list(args.get("strategic_priorities", [])),
                    constraints=list(args.get("constraints", [])),
                ), confirmed_by=args.get("confirmed_by")),
            ),
            ToolSpec(
                name="board_output",
                description="Run a multi-option board decision packet.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "problem": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "recommended_option_index": {"type": "integer"},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "prior_board_decisions": {"type": "array", "items": {"type": "string"}},
                        "strategic_risks": {"type": "array", "items": {"type": "string"}},
                        "confirmed_by": {"type": "string"},
                    },
                    "required": ["title", "company", "problem"],
                },
                handler=lambda args: _run_session(state, BoardBrief(
                    title=args["title"],
                    company=args["company"],
                    problem=args["problem"],
                    options=list(args.get("options", [])),
                    recommended_option_index=int(args.get("recommended_option_index", 0)),
                    open_questions=list(args.get("open_questions", [])),
                    prior_board_decisions=list(args.get("prior_board_decisions", [])),
                    strategic_risks=list(args.get("strategic_risks", [])),
                    strategic_priorities=list(args.get("strategic_priorities", [])),
                    constraints=list(args.get("constraints", [])),
                ), confirmed_by=args.get("confirmed_by")),
            ),
            ToolSpec(
                name="lock",
                description="Apply third-party validation to an existing decision id.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "decision_id": {"type": "string"},
                        "validator": {"type": "string"},
                        "item": {"type": "string"},
                        "rationale": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["decision_id", "validator", "item", "rationale"],
                },
                handler=lambda args: _lock_case(state, args),
            ),
            ToolSpec(
                name="decisions",
                description=(
                    "Read CHP decision-ledger records — the durable, integrity-checked "
                    "audit of every gated CFO session (list, or get by decision id)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "decision_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": [],
                },
                handler=lambda args: _read_decisions(state, args),
            ),
        ],
    )
    server.serve()


def _run_session(state: MeshCFOState, brief: Any, confirmed_by: str | None = None) -> dict[str, Any]:
    system = CFOOperatingSystem(
        agents=_default_agents(),
        registry=state.registry,
        context=ContextEngine(),
        ledger=state.ledger,
        company_name=brief.company,
    )
    report = system.run(brief, confirmed_by=confirmed_by)
    state.save()
    return {
        "task": brief.task_type.value,
        "decision_id": report.case.decision_id,
        "lock_state": report.case.status.value,
        "foundation_score": report.case.foundation_score,
        "r0_verdict": report.r0_verdict.value,
        "foundation_verdict": report.foundation_verdict.value,
        "chp": report.hardening,
        "initial_packet": report.initial_packet,
        "artifact_markdown": report.artifact.render(),
        "audit_markdown": report.audit.render(),
        "case": report.case.to_dict(),
    }


def _read_decisions(state: MeshCFOState, args: dict[str, Any]) -> Any:
    """Read decision-ledger records; every read re-validates integrity."""
    decision_id = args.get("decision_id")
    if decision_id:
        record = state.decisions.get(str(decision_id))
        if record is None:
            raise KeyError(f"unknown decision_id: {decision_id}")
        return record
    return state.decisions.list(limit=int(args.get("limit", 20)))


def _lock_case(state: MeshCFOState, args: dict[str, Any]) -> dict[str, Any]:
    system = CFOOperatingSystem(agents=_default_agents(), registry=state.registry, ledger=state.ledger)
    case = system.lock(
        args["decision_id"],
        validator=args["validator"],
        item=args["item"],
        rationale=args["rationale"],
        confirm=bool(args.get("confirm", True)),
    )
    state.save()
    return {"decision_id": case.decision_id, "status": case.status.value, "locked_decisions": case.locked_decisions}


def _default_agents() -> list[Any]:
    from demo import ComplianceAgent, FinanceAgent, StrategyAgent

    return [FinanceAgent(), StrategyAgent(), ComplianceAgent()]


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        stripped = line.decode("utf-8", errors="replace").strip()
        if not stripped:
            break
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        headers[key.lower().strip()] = value.strip()
    length = int(headers.get("content-length", "0"))
    payload = sys.stdin.buffer.read(length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def _respond(message: dict[str, Any], result: dict[str, Any]) -> None:
    payload = json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}, default=str).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _error(message: dict[str, Any], code: int, detail: str) -> None:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": code, "message": detail}},
        default=str,
    ).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cfo-os mcp", description="Run the MeshCFO MCP server.")
    parser.add_argument("--registry", default=".chp_registry.json", help="Path to the CHP registry file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve(args.registry)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
