"""Online scorers over production runs (spec §13.4, §14.4) and the wrong-auto-accept monitor (§9.5, §11.3).

Every production trace is scored for:

- **schema-valid rate**: the share of agent runs (orchestrator and sub-agents) that ended with output that passed
  validation, rather than exhausting their output retries;
- **grounding-retry rate**: the share of extracted values whose deterministic span match failed, so grounding was
  retried with the aligner model (§7.3); the share dropped for want of any span is reported beside it;
- **budget exceeded**: the run ended on a budget (``UsageLimits`` or the ``BudgetGuard``, §13.6);
- **policy violations** of the expected agent behaviour (§13.4): ``make_plan`` first; tier A tasks first, never
  before a dependency; ``verify_facts`` on each slot group before moving on; a tier A gap followed up (search,
  re-extract, mark unknown or skip); after the tasks ``check_constraints``, ``run_sensitivity`` and
  ``draft_followups``, in that order, then finish; no tool outside the registry and no op outside RETRACT,
  MARK_UNKNOWN and CONFIRM.

A run is read from its spans (OpenTelemetry spans as exported, or Braintrust spans fetched for a trace; attributes
are read through ``advisor_intel.telemetry_attrs`` when installed), from the orchestrator's message history (the
checkpoint), or from a ``PipelineResult``. ``register_online_scorers`` defines the Braintrust code scorers that the
project's online scoring rules run on every production trace (``braintrust push
advisor_intel/evals/online_push.py``); Braintrust alerts fire on their thresholds.

The wrong-auto-accept rate comes from spot-checked reviews (1 in 10 shows every auto-accepted fact, §11.3): the
share of those calls with any auto-accepted fact the adviser rejected or corrected. When it exceeds α over the
last 100 spot-checked calls, ``AutoAcceptMonitor`` alerts and switches auto-accept off (§14.4).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel

from ..schema.slots import Catalog, load_catalog
from ._runtime import data_dir, get, lazy_module, run_sync, write_json

# The tool registry (§13.3) and the expected behaviour (§13.4).
PLANNING_TOOLS = ("make_plan", "view_plan", "update_plan", "view_profile", "note")
RETRIEVAL_TOOLS = ("get_segments", "search_transcript", "get_entity")
WORK_TOOLS = (
    "extract_slot_group",
    "verify_facts",
    "check_constraints",
    "apply_state_ops",
    "run_sensitivity",
    "draft_followups",
    "lookup_term",
)
POLICY_TOOLS = frozenset(PLANNING_TOOLS + RETRIEVAL_TOOLS + WORK_TOOLS)
FINAL_SEQUENCE = ("check_constraints", "run_sensitivity", "draft_followups")
AGENT_OPS = frozenset({"RETRACT", "MARK_UNKNOWN", "CONFIRM"})
OUTPUT_TOOL_PREFIX = "final_result"  # Pydantic AI's output tool, when a provider returns the output through a tool
ALIGNER_AGENT = "aligner"
ORCHESTRATOR = "orchestrator"

BUDGET_ERROR = re.compile(r"UsageLimitExceeded|BudgetExceeded|budget spent|exceed(?:s|ed)? the \w+_limit|_limit of \d", re.I)
SCHEMA_ERROR = re.compile(
    r"UnexpectedModelBehavior|output validation|ValidationError|Exceeded maximum (?:output )?retries|validation error", re.I
)
GROUNDING_DROP = re.compile(r"no candidate segments|no span matched|found no span|no located span", re.I)
LOOP_BLOCKED = "Error: you have called"

Rule = Literal[
    "make_plan_first",
    "tier_a_first",
    "dependency_order",
    "verify_after_group",
    "gap_not_followed_up",
    "finish_sequence",
    "tool_outside_policy",
    "op_outside_policy",
]
RULES: tuple[str, ...] = Rule.__args__  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- spans


class TraceSpan(BaseModel):
    """One span of a production trace, whichever exporter it came through."""

    span_id: str
    parent_id: str | None = None
    name: str = ""
    start: float = 0.0
    end: float | None = None
    attributes: dict[str, Any] = {}
    error: str | None = None  # "Type: message" when the span recorded an exception or an error status


def _telemetry_read(attrs: Mapping[str, Any], field: str, names: Sequence[str]) -> Any:
    """A logical field through ``advisor_intel.telemetry_attrs`` (the one mapping module, §14.4) when installed,
    else under the GenAI semantic-convention names given."""
    mod = lazy_module("advisor_intel.telemetry_attrs")
    if mod is not None and field in getattr(mod, "FIELDS", {}):
        v = mod.read(attrs, field)
        if v is not None:
            return v
    for n in names:
        if attrs.get(n) not in (None, ""):
            return attrs[n]
    return None


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def spans_from_otel(spans: Iterable[Any]) -> list[TraceSpan]:
    """OpenTelemetry SDK spans (``ReadableSpan``, e.g. from an ``InMemorySpanExporter``) as trace spans."""
    out = []
    for s in spans:
        ctx = s.get_span_context() if hasattr(s, "get_span_context") else s.context
        error = None
        for ev in s.events or ():
            if ev.name == "exception":
                a = ev.attributes or {}
                error = f"{a.get('exception.type', 'Exception')}: {a.get('exception.message', '')}"
        status = getattr(s, "status", None)
        if error is None and status is not None and getattr(status.status_code, "name", "") == "ERROR":
            error = status.description or "error"
        out.append(
            TraceSpan(
                span_id=format(ctx.span_id, "016x"),
                parent_id=format(s.parent.span_id, "016x") if s.parent is not None else None,
                name=s.name,
                start=(s.start_time or 0) / 1e9,
                end=(s.end_time / 1e9) if s.end_time else None,
                attributes=dict(s.attributes or {}),
                error=error,
            )
        )
    return sorted(out, key=lambda x: x.start)


def spans_from_braintrust(rows: Iterable[Any]) -> list[TraceSpan]:
    """Braintrust spans (``SpanData`` from ``trace.get_spans()``, or log rows) as trace spans.

    OTLP ingestion keeps a tool span's arguments and result as the row's input and output and the other GenAI
    attributes in its metadata; this folds them back into OpenTelemetry attribute names."""
    out = []
    for r in rows:
        sa = dict(get(r, "span_attributes") or {})
        meta = dict(get(r, "metadata") or {})
        attrs = dict(meta)
        name = str(sa.get("name") or meta.get("name") or "")
        tool = _telemetry_read(meta, "tool", ("gen_ai.tool.name",))
        if tool is None and name.startswith("execute_tool "):
            tool = name.removeprefix("execute_tool ")
        if tool is not None or sa.get("type") == "tool":
            attrs.setdefault("gen_ai.operation.name", "execute_tool")
            attrs.setdefault("gen_ai.tool.name", tool or name)
            if get(r, "input") is not None:
                attrs.setdefault("gen_ai.tool.call.arguments", get(r, "input"))
            if get(r, "output") is not None:
                attrs.setdefault("gen_ai.tool.call.result", get(r, "output"))
        elif name.startswith("invoke_agent ") or meta.get("gen_ai.agent.name"):
            attrs.setdefault("gen_ai.operation.name", "invoke_agent")
            attrs.setdefault("gen_ai.agent.name", meta.get("gen_ai.agent.name") or name.removeprefix("invoke_agent "))
        metrics = dict(get(r, "metrics") or {})
        err = get(r, "error")
        parents = get(r, "span_parents") or []
        out.append(
            TraceSpan(
                span_id=str(get(r, "span_id") or get(r, "id") or len(out)),
                parent_id=str(parents[0]) if parents else None,
                name=name,
                start=float(metrics.get("start") or 0.0),
                end=float(metrics["end"]) if metrics.get("end") is not None else None,
                attributes=attrs,
                error=None if err in (None, "", {}) else (err if isinstance(err, str) else json.dumps(err, default=str)),
            )
        )
    return sorted(out, key=lambda x: x.start)


# --------------------------------------------------------------------------- the run


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    ok: bool = True
    result: Any = None


class AgentRun(BaseModel):
    agent: str
    error: str | None = None

    @property
    def schema_failed(self) -> bool:
        return bool(self.error) and bool(SCHEMA_ERROR.search(self.error or ""))

    @property
    def budget_stopped(self) -> bool:
        return bool(self.error) and bool(BUDGET_ERROR.search(self.error or ""))


class RunFact(BaseModel):
    fact_id: str
    slot_id: str
    source: str = "call"
    status: str = ""
    grounded: bool = False
    field_retries: int = 0
    auto_accepted: bool = False
    review_status: str = "pending"


class RunTrace(BaseModel):
    """One production run, as the online scorers read it. ``args_known`` is False when only tool names are known."""

    call_id: str = ""
    run_id: str = ""
    tool_calls: list[ToolCall] = []
    args_known: bool = True
    agent_runs: list[AgentRun] = []
    agents_known: bool = True
    partial: bool | None = None
    reason: str = ""
    finished: bool | None = None
    facts: list[RunFact] = []
    facts_known: bool = False

    # ------------------------------------------------------------ constructors

    @classmethod
    def from_spans(cls, spans: Sequence[TraceSpan], *, profile: Any = None, outcome: Any = None, **kw: Any) -> "RunTrace":
        """A run from its trace: tool spans in start order, agent-run spans and their errors."""
        calls, agents = [], []
        call_id = run_id = ""
        for s in sorted(spans, key=lambda x: x.start):
            a = s.attributes
            call_id = call_id or str(_telemetry_read(a, "call_id", ("call_id",)) or "")
            run_id = run_id or str(_telemetry_read(a, "workflow_id", ("workflow_id", "run_id")) or "")
            op = a.get("gen_ai.operation.name")
            tool = _telemetry_read(a, "tool", ("gen_ai.tool.name",))
            if op == "execute_tool" or (tool and s.name.startswith(("execute_tool", "running tool"))):
                args = _json(a.get("gen_ai.tool.call.arguments", a.get("tool_arguments")))
                result = _json(a.get("gen_ai.tool.call.result", a.get("tool_response")))
                blocked = isinstance(result, str) and result.startswith(LOOP_BLOCKED)
                calls.append(
                    ToolCall(tool=str(tool), args=args if isinstance(args, dict) else {}, ok=s.error is None and not blocked, result=result)
                )
            elif op == "invoke_agent" or s.name.startswith(("invoke_agent", "agent run")):
                agent = _telemetry_read(a, "agent", ("gen_ai.agent.name", "agent_name")) or s.name.removeprefix("invoke_agent ").strip()
                agents.append(AgentRun(agent=str(agent), error=s.error))
        kw = {"call_id": call_id, "run_id": run_id} | {k: v for k, v in kw.items() if v not in (None, "")}
        run = cls(tool_calls=calls, agent_runs=agents, **kw)
        return run._with(profile=profile, outcome=outcome)

    @classmethod
    def from_messages(cls, messages: Sequence[Any], *, profile: Any = None, outcome: Any = None, **kw: Any) -> "RunTrace":
        """A run from the orchestrator's message history (Pydantic AI messages, or their JSON as checkpointed)."""
        from pydantic_ai.messages import (
            ModelMessagesTypeAdapter,
            ModelRequest,
            ModelResponse,
            RetryPromptPart,
            ToolCallPart,
            ToolReturnPart,
        )

        msgs = list(messages)
        if msgs and isinstance(msgs[0], dict):
            msgs = ModelMessagesTypeAdapter.validate_python(msgs)
        calls: dict[str, ToolCall] = {}
        order: list[str] = []
        finished = False
        for m in msgs:
            if isinstance(m, ModelResponse):
                for p in m.parts:
                    if isinstance(p, ToolCallPart):
                        calls[p.tool_call_id] = ToolCall(tool=p.tool_name, args=p.args_as_dict(), ok=True)
                        order.append(p.tool_call_id)
            elif isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, ToolReturnPart) and p.tool_call_id in calls:
                        content = _json(p.content) if isinstance(p.content, str) else p.content
                        blocked = isinstance(content, str) and content.startswith(LOOP_BLOCKED)
                        calls[p.tool_call_id] = calls[p.tool_call_id].model_copy(
                            update={"result": content, "ok": getattr(p, "outcome", "success") == "success" and not blocked}
                        )
                    elif isinstance(p, RetryPromptPart) and p.tool_call_id in calls:
                        calls[p.tool_call_id] = calls[p.tool_call_id].model_copy(update={"ok": False, "result": p.content})
        if msgs and isinstance(msgs[-1], ModelResponse) and not any(isinstance(p, ToolCallPart) for p in msgs[-1].parts):
            finished = True
        run = cls(tool_calls=[calls[i] for i in order], agents_known=False, finished=finished, **kw)
        return run._with(profile=profile, outcome=outcome)

    @classmethod
    def from_result(cls, result: Any, *, spans: Sequence[TraceSpan] | None = None) -> "RunTrace":
        """A run from a ``PipelineResult`` (or the harness's ``AgentResult``): the spans when given, else the tool
        names it records (arguments unknown), with the outcome and the final profile."""
        outcome = get(result, "outcome")
        profile = get(result, "profile")
        ids = {"call_id": str(get(result, "call_id") or get(profile, "call_id") or ""), "run_id": str(get(result, "run_id") or "")}
        if spans:
            return cls.from_spans(spans, profile=profile, outcome=outcome, **ids)
        names = list(get(result, "tool_calls") or [])
        state = get(result, "state") or {}
        if not names and state.get("events"):
            names = [e["tool"] if isinstance(e, dict) else e.tool for e in state["events"]]
        events = state.get("events") or []
        oks = [bool(e.get("ok", True)) if isinstance(e, dict) else bool(e.ok) for e in events]
        calls = [ToolCall(tool=n, ok=oks[i] if i < len(oks) else True) for i, n in enumerate(names)]
        run = cls(tool_calls=calls, args_known=False, agents_known=False, **ids)
        return run._with(profile=profile, outcome=outcome)

    def _with(self, *, profile: Any = None, outcome: Any = None) -> "RunTrace":
        upd: dict[str, Any] = {}
        if outcome is not None:
            upd["partial"] = bool(get(outcome, "partial"))
            upd["reason"] = str(get(outcome, "reason") or "")
        if profile is not None:
            facts = get(profile, "facts") or {}
            upd["facts"] = [
                RunFact(
                    fact_id=f.fact_id,
                    slot_id=f.slot_id,
                    source=f.source,
                    status=f.status,
                    grounded=bool(f.evidence),
                    field_retries=int(f.field_retries or 0),
                    auto_accepted=bool(f.auto_accepted),
                    review_status=f.review.status,
                )
                for f in facts.values()
            ]
            upd["facts_known"] = True
        return self.model_copy(update=upd) if upd else self


# --------------------------------------------------------------------------- run-level rates


class Rate(BaseModel):
    value: float | None
    hits: int = 0
    total: int = 0
    detail: list[str] = []


def schema_valid_rate(run: RunTrace) -> Rate:
    """Agent runs whose output passed validation / agent runs that produced or failed to produce output (runs stopped
    by a budget or a provider error are left out)."""
    if not run.agents_known:
        return Rate(value=None, detail=["agent runs are not in this record"])
    judged = [a for a in run.agent_runs if a.error is None or a.schema_failed]
    ok = [a for a in judged if a.error is None]
    return Rate(
        value=len(ok) / len(judged) if judged else None,
        hits=len(ok),
        total=len(judged),
        detail=[f"{a.agent}: {a.error}" for a in judged if a.error][:20],
    )


def _grounding_drops(run: RunTrace) -> list[str]:
    drops = []
    for c in run.tool_calls:
        if c.tool == "extract_slot_group" and isinstance(c.result, dict):
            drops += [str(d) for d in c.result.get("dropped") or [] if GROUNDING_DROP.search(str(d))]
    return drops


def grounding_retry_rate(run: RunTrace) -> tuple[Rate, Rate]:
    """(retry rate, drop rate) over the run's grounding attempts (§7.3): a retry is a value whose deterministic span
    match failed and went to the aligner model; a drop is a value no span supports. Attempts are the values grounded
    (call facts with evidence, from the profile; else the facts each extraction reported) plus the values dropped."""
    if not run.agents_known or not run.args_known:
        return Rate(value=None, detail=["spans are not in this record"]), Rate(value=None)
    aligner = sum(1 for a in run.agent_runs if a.agent == ALIGNER_AGENT)
    drops = _grounding_drops(run)
    if run.facts_known:
        grounded = sum(1 for f in run.facts if f.source == "call" and f.grounded)
    else:
        grounded = sum(
            len(c.result.get("new_fact_ids") or []) for c in run.tool_calls if c.tool == "extract_slot_group" and isinstance(c.result, dict)
        )
    attempts = grounded + len(drops)
    if not attempts:
        return Rate(value=None), Rate(value=None)
    retried = min(aligner, attempts)
    return (
        Rate(value=retried / attempts, hits=retried, total=attempts),
        Rate(value=len(drops) / attempts, hits=len(drops), total=attempts, detail=drops[:20]),
    )


def budget_exceeded(run: RunTrace) -> float | None:
    """1.0 when the run ended on a budget (§13.6), else 0.0; None when nothing says how the run ended."""
    if run.partial is not None and run.reason and BUDGET_ERROR.search(run.reason):
        return 1.0
    if any(a.budget_stopped for a in run.agent_runs):
        return 1.0
    if run.partial is None and not run.agents_known:
        return None
    return 0.0


# --------------------------------------------------------------------------- the §13.4 policy


class Violation(BaseModel):
    rule: Rule
    index: int | None = None  # position in the tool-call sequence
    detail: str


class PolicyReport(BaseModel):
    violations: list[Violation] = []
    warnings: list[Violation] = []  # advisory rules (a tier A gap left without a follow-up): reported, not scored
    n_calls: int = 0
    checked: list[str] = []  # rules that could be checked on this record

    @property
    def passed(self) -> bool:
        return not self.violations

    @property
    def score(self) -> float:
        return 1.0 if self.passed else 0.0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v.rule] = out.get(v.rule, 0) + 1
        return out


class _Task(BaseModel):
    task_id: str
    group: str
    status: str = "todo"
    depends_on: list[str] = []


def _plan_from(result: Any) -> dict[str, _Task] | None:
    if not isinstance(result, dict) or not isinstance(result.get("tasks"), list):
        return None
    out = {}
    for t in result["tasks"]:
        if isinstance(t, dict) and t.get("task_id"):
            out[t["task_id"]] = _Task(
                task_id=t["task_id"],
                group=str(t.get("slot_group") or t["task_id"].split(":")[0]),
                status=str(t.get("status") or "todo"),
                depends_on=list(t.get("depends_on") or []),
            )
    return out


def _tier_a_groups(catalog: Catalog) -> set[str]:
    return {s.group for s in catalog.tier_a()}


def _requires(plan: Mapping[str, _Task], task_id: str) -> set[str]:
    """The tasks ``task_id`` waits for, transitively."""
    seen: set[str] = set()
    stack = list(plan[task_id].depends_on) if task_id in plan else []
    while stack:
        t = stack.pop()
        if t not in seen:
            seen.add(t)
            stack.extend(plan[t].depends_on if t in plan else [])
    return seen


def _first_call_ok(calls: Sequence[ToolCall]) -> tuple[bool, int | None, str]:
    """make_plan first; a resumed run (§13.2) may open with view_plan when its result shows the plan already exists."""
    real = [(i, c) for i, c in enumerate(calls) if not c.tool.startswith(OUTPUT_TOOL_PREFIX)]
    if not real:
        return False, None, "the run made no tool calls"
    i, c = real[0]
    resumed = c.tool == "view_plan" and bool((_plan_from(c.result) or {}))
    if c.tool == "make_plan" or resumed:
        return True, i, ""
    return False, i, f"the first tool call was {c.tool}, not make_plan"


class _PolicyWalk:
    """Replays a tool-call sequence against the plan it produced, collecting violations of §13.4."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.tier_a = _tier_a_groups(catalog)
        self.plan: dict[str, _Task] = {}
        self.pending: dict[str, int] = {}  # slot group extracted since its last verify_facts → index of that extraction
        self.reported: set[str] = set()  # groups already reported as unverified
        self.gaps: dict[str, int] = {}  # slot group whose extraction reported tier A gaps → index
        self.violations: list[Violation] = []
        self.warnings: list[Violation] = []

    def group_of(self, task_id: str) -> str:
        t = self.plan.get(task_id)
        return t.group if t is not None else task_id.split(":")[0]

    def extract(self, i: int, c: ToolCall) -> None:
        tid = str(c.args.get("task_id") or "")
        task, group = self.plan.get(tid), self.group_of(tid)
        unmet = [d for d in (task.depends_on if task else []) if d in self.plan and self.plan[d].status in ("todo", "running")]
        if unmet:
            self.violations.append(
                Violation(rule="dependency_order", index=i, detail=f"{tid} ran before {', '.join(unmet)}, which it depends on")
            )
        if task is not None and group not in self.tier_a:
            waiting = [
                t.task_id
                for t in self.plan.values()
                if t.group in self.tier_a and t.status == "todo" and tid not in _requires(self.plan, t.task_id)
            ]
            if waiting:
                self.violations.append(
                    Violation(
                        rule="tier_a_first", index=i, detail=f"{tid} ran while tier A tasks were still todo: {', '.join(waiting[:5])}"
                    )
                )
        self.unverified_before(i, f"moved on to {group}", keep=group)
        if not c.ok:
            if task is not None and isinstance(c.result, str) and "depends on" not in c.result:
                task.status = "failed"
            return
        if task is not None:
            task.status = "done"
        self.pending.setdefault(group, i)
        self.gaps.pop(group, None)  # a re-extraction is a follow-up of the group's earlier gap
        if isinstance(c.result, dict) and c.result.get("tier_a_gaps"):
            self.gaps[group] = i

    def verify(self, c: ToolCall) -> None:
        group, ids = c.args.get("slot_group"), c.args.get("fact_ids")
        if group:
            done = {str(group)}
        elif ids:
            done = set()
            for fid in ids:
                try:
                    done.add(self.catalog.slot(str(fid).split("#", 1)[0]).group)
                except KeyError:
                    continue
        else:
            done = set(self.pending)
        for g in done:
            self.pending.pop(g, None)
            self.reported.discard(g)

    def unverified_before(self, i: int, what: str, *, keep: str | None = None) -> None:
        for g, j in list(self.pending.items()):
            if g != keep and g not in self.reported:
                self.violations.append(
                    Violation(rule="verify_after_group", index=i, detail=f"{what} before verify_facts on {g} (extracted at call {j})")
                )
                self.reported.add(g)

    def final_tool(self, i: int, c: ToolCall) -> None:
        self.unverified_before(i, c.tool)
        for g, j in sorted(self.gaps.items(), key=lambda kv: kv[1]):
            self.warnings.append(
                Violation(
                    rule="gap_not_followed_up", index=j, detail=f"tier A gaps in {g} were not searched for, re-extracted or marked unknown"
                )
            )
        self.gaps.clear()

    def end(self) -> None:
        for g, j in self.pending.items():
            if g not in self.reported and g in self.tier_a:
                self.violations.append(Violation(rule="verify_after_group", index=j, detail=f"tier A group {g} was never verified"))


def check_policy(run: RunTrace, *, catalog: Catalog | None = None) -> PolicyReport:
    """Check a run's tool-call sequence against the expected behaviour of §13.4 (see the module docstring).

    With tool names only (``args_known`` False), the rules that need arguments (tier A order, dependencies,
    per-group verification, op types) are not checked and are left out of ``checked``."""
    catalog = catalog or load_catalog()
    calls = run.tool_calls
    v: list[Violation] = []
    checked = ["make_plan_first", "finish_sequence", "tool_outside_policy"]
    ok, i0, why = _first_call_ok(calls)
    if not ok:
        v.append(Violation(rule="make_plan_first", index=i0, detail=why))
    for i, c in enumerate(calls):
        if c.tool not in POLICY_TOOLS and not c.tool.startswith(OUTPUT_TOOL_PREFIX):
            v.append(Violation(rule="tool_outside_policy", index=i, detail=f"{c.tool} is not in the tool registry (§13.3)"))
    walk = _PolicyWalk(catalog)
    if run.args_known:
        checked += ["tier_a_first", "dependency_order", "verify_after_group", "op_outside_policy"]
        for i, c in enumerate(calls):
            snapshot = _plan_from(c.result)
            if snapshot is not None and c.tool in ("make_plan", "view_plan", "update_plan"):
                walk.plan = snapshot
            if c.tool == "extract_slot_group":
                walk.extract(i, c)
            elif c.tool == "verify_facts":
                walk.verify(c)
            elif c.tool in ("search_transcript", "apply_state_ops", "update_plan"):
                walk.gaps.clear()  # the agent looked for the missing evidence, marked it unknown or re-planned
            elif c.tool in FINAL_SEQUENCE:
                walk.final_tool(i, c)
            if c.tool == "apply_state_ops":
                for op in c.args.get("ops") or []:
                    kind = str(op.get("op", "")).upper() if isinstance(op, dict) else ""
                    if kind not in AGENT_OPS:
                        v.append(
                            Violation(
                                rule="op_outside_policy",
                                index=i,
                                detail=f"apply_state_ops sent {kind or 'an op'}; only RETRACT, MARK_UNKNOWN, CONFIRM",
                            )
                        )
        walk.end()
        v += walk.violations
    names = [c.tool for c in calls]
    if not run.partial:
        last_extract = max((i for i, n in enumerate(names) if n == "extract_slot_group"), default=-1)
        firsts = [next((i for i, n in enumerate(names) if n == t and i > last_extract), None) for t in FINAL_SEQUENCE]
        missing = [t for t, i in zip(FINAL_SEQUENCE, firsts) if i is None]
        if missing:
            v.append(
                Violation(rule="finish_sequence", index=None, detail=f"after the last extraction the run never called {', '.join(missing)}")
            )
        elif not all(a < b for a, b in zip(firsts, firsts[1:])):  # type: ignore[operator]
            v.append(
                Violation(
                    rule="finish_sequence",
                    index=min(i for i in firsts if i is not None),
                    detail="check_constraints, run_sensitivity and draft_followups ran out of order",
                )
            )
        if run.finished is False:
            v.append(Violation(rule="finish_sequence", index=None, detail="the run ended without finishing"))
    return PolicyReport(
        violations=sorted(v, key=lambda x: (x.index is None, x.index or 0)), warnings=walk.warnings, n_calls=len(calls), checked=checked
    )


# --------------------------------------------------------------------------- scoring runs


class RunScores(BaseModel):
    call_id: str = ""
    run_id: str = ""
    schema_valid_rate: float | None = None
    grounding_retry_rate: float | None = None
    grounding_drop_rate: float | None = None
    budget_exceeded: float | None = None
    policy: float | None = None
    policy_violations: dict[str, int] = {}
    policy_detail: list[str] = []


def score_run(run: RunTrace, *, catalog: Catalog | None = None) -> RunScores:
    retry, drop = grounding_retry_rate(run)
    pol = check_policy(run, catalog=catalog)
    return RunScores(
        call_id=run.call_id,
        run_id=run.run_id,
        schema_valid_rate=schema_valid_rate(run).value,
        grounding_retry_rate=retry.value,
        grounding_drop_rate=drop.value,
        budget_exceeded=budget_exceeded(run),
        policy=pol.score,
        policy_violations=pol.counts(),
        policy_detail=[f"{x.rule}: {x.detail}" for x in pol.violations][:20],
    )


def summarise_runs(scores: Sequence[RunScores]) -> dict[str, Any]:
    """Rates across runs, for a dashboard or a nightly check: means of the per-run rates and the violation counts."""

    def mean(xs: Iterable[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return sum(vals) / len(vals) if vals else None

    counts: dict[str, int] = {}
    for s in scores:
        for k, n in s.policy_violations.items():
            counts[k] = counts.get(k, 0) + n
    return {
        "n_runs": len(scores),
        "schema_valid_rate": mean(s.schema_valid_rate for s in scores),
        "grounding_retry_rate": mean(s.grounding_retry_rate for s in scores),
        "grounding_drop_rate": mean(s.grounding_drop_rate for s in scores),
        "budget_exceeded_rate": mean(s.budget_exceeded for s in scores),
        "policy_violation_rate": mean(None if s.policy is None else 1.0 - s.policy for s in scores),
        "policy_violations": counts,
    }


# --------------------------------------------------------------------------- the wrong-auto-accept monitor


WRONG_ACTIONS = frozenset({"reject", "reattribute", "remove", "change_status", "mark_unknown"})


class SpotCheck(BaseModel):
    """One spot-checked review (§11.3): which auto-accepted facts the adviser found wrong."""

    call_id: str
    run_id: str = ""
    at: str = ""
    auto_accepted: list[str] = []
    wrong: list[str] = []

    @property
    def any_wrong(self) -> bool:
        return bool(self.wrong)


def spot_check(
    call_id: str,
    auto_accepted: Iterable[str],
    events: Iterable[Any],
    *,
    run_id: str = "",
    at: datetime | None = None,
    is_correct: Callable[[Any], bool | None] | None = None,
) -> SpotCheck:
    """The auto-accepted facts a spot-checked review corrected. An event is a review event (model or dict with
    ``fact_id`` and ``action``); ``is_correct`` (e.g. the §9.3 label: an edit inside the original's p10–p90 is
    correct) decides edits, which otherwise count as wrong. Accepting a fact is right."""
    auto = list(dict.fromkeys(auto_accepted))
    wrong: list[str] = []
    for e in events:
        fid, action = str(get(e, "fact_id") or ""), str(get(e, "action") or "")
        if fid not in auto or fid in wrong:
            continue
        verdict = is_correct(e) if is_correct is not None else None
        if verdict is None:
            verdict = not (action in WRONG_ACTIONS or action == "edit")
        if not verdict:
            wrong.append(fid)
    return SpotCheck(call_id=call_id, run_id=run_id, at=(at or datetime.now(timezone.utc)).isoformat(), auto_accepted=auto, wrong=wrong)


class AutoAcceptSwitch(Protocol):
    def enabled(self) -> bool: ...

    def disable(self, reason: str) -> None: ...


class FileSwitch:
    """The auto-accept switch as a JSON file (the deployment can point a config store at the same protocol)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "online" / "auto_accept.json")

    def state(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {"enabled": True}

    def enabled(self) -> bool:
        return bool(self.state().get("enabled", True))

    def disable(self, reason: str) -> None:
        write_json(self.path, {"enabled": False, "reason": reason, "at": datetime.now(timezone.utc).isoformat()})

    def enable(self, reason: str = "re-enabled by hand") -> None:
        write_json(self.path, {"enabled": True, "reason": reason, "at": datetime.now(timezone.utc).isoformat()})


class MonitorStatus(BaseModel):
    n_calls: int
    n_wrong: int
    rate: float | None
    alpha: float
    window: int
    alert: bool
    auto_accept_enabled: bool
    message: str = ""


class AutoAcceptMonitor:
    """The realised wrong-auto-accept rate over the last ``window`` spot-checked calls, against α (§9.5, §14.4).

    It alerts, and switches auto-accept off, as soon as the wrong calls in the window exceed α·window: from then on
    the rate over those 100 calls exceeds α whatever the remaining calls show. Re-enabling is a human decision."""

    def __init__(
        self,
        *,
        alpha: float = 0.1,
        window: int = 100,
        path: Path | None = None,
        switch: AutoAcceptSwitch | None = None,
        on_alert: Callable[[MonitorStatus], None] | None = None,
    ) -> None:
        self.alpha, self.window = alpha, window
        self.path = path or (data_dir() / "online" / "spot_checks.jsonl")
        self.switch = switch or FileSwitch(self.path.with_name("auto_accept.json"))
        self.on_alert = on_alert

    def history(self) -> list[SpotCheck]:
        if not self.path.exists():
            return []
        return [SpotCheck.model_validate_json(line) for line in self.path.read_text().splitlines() if line.strip()]

    def status(self) -> MonitorStatus:
        recent = self.history()[-self.window :]
        n_wrong = sum(1 for c in recent if c.any_wrong)
        alert = n_wrong > self.alpha * self.window
        rate = n_wrong / len(recent) if recent else None
        msg = (
            f"{n_wrong} of the last {len(recent)} spot-checked calls had a wrong auto-accepted fact: above α = {self.alpha:g} over "
            f"{self.window} calls; auto-accept is off until someone re-enables it"
            if alert
            else ""
        )
        return MonitorStatus(
            n_calls=len(recent),
            n_wrong=n_wrong,
            rate=rate,
            alpha=self.alpha,
            window=self.window,
            alert=alert,
            auto_accept_enabled=self.switch.enabled(),
            message=msg,
        )

    def record(self, check: SpotCheck) -> MonitorStatus:
        """Add one spot-checked call (idempotent per call and run) and act on the new status."""
        seen = {(c.call_id, c.run_id) for c in self.history()}
        if (check.call_id, check.run_id) not in seen:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(check.model_dump_json() + "\n")
        st = self.status()
        if st.alert and self.switch.enabled():
            self.switch.disable(st.message)
            st = st.model_copy(update={"auto_accept_enabled": False})
            if self.on_alert is not None:
                self.on_alert(st)
        return st


def wrong_auto_accept_score(check: SpotCheck) -> float:
    """The per-call online score: 1 when a spot-checked call had any wrong auto-accepted fact (its mean is the rate)."""
    return 1.0 if check.any_wrong else 0.0


# --------------------------------------------------------------------------- Braintrust online scorers


class OnlineScorerArgs(BaseModel):
    """What a Braintrust online scoring rule passes a code scorer for a trace's root span."""

    input: Any = None
    output: Any = None
    expected: Any = None
    metadata: dict[str, Any] | None = None


def _run_for(output: Any, metadata: Mapping[str, Any] | None, trace: Any) -> RunTrace:
    """The run behind a root span: its trace's spans when the scorer is given the trace, else a run record the
    root span carries in its output (tool calls, agent runs, outcome)."""
    meta = dict(metadata or {})
    if trace is not None and hasattr(trace, "get_spans"):
        spans = spans_from_braintrust(run_sync(trace.get_spans()))
        run = RunTrace.from_spans(spans)
        out = output if isinstance(output, dict) else {}
        if isinstance(out.get("outcome"), dict):
            run = run.model_copy(update={"partial": bool(out["outcome"].get("partial")), "reason": str(out["outcome"].get("reason") or "")})
        return run.model_copy(
            update={"call_id": run.call_id or str(meta.get("call_id") or ""), "run_id": run.run_id or str(meta.get("workflow_id") or "")}
        )
    if isinstance(output, dict) and "tool_calls" in output:
        return RunTrace.model_validate({k: v for k, v in output.items() if k in RunTrace.model_fields})
    return RunTrace(args_known=False, agents_known=False)


def _score(name: str, value: float | None, **extra: Any) -> dict[str, Any] | None:
    return None if value is None else {"name": name, "score": value, "metadata": extra}


def policy_scorer(
    input: Any = None, output: Any = None, expected: Any = None, metadata: Any = None, trace: Any = None, **_: Any
) -> dict[str, Any] | None:
    run = _run_for(output, metadata, trace)
    rep = check_policy(run)
    return _score("policy", rep.score, violations=[f"{v.rule}: {v.detail}" for v in rep.violations][:20], checked=rep.checked)


def schema_valid_scorer(
    input: Any = None, output: Any = None, expected: Any = None, metadata: Any = None, trace: Any = None, **_: Any
) -> dict[str, Any] | None:
    r = schema_valid_rate(_run_for(output, metadata, trace))
    return _score("schema_valid_rate", r.value, failures=r.detail)


def grounding_retry_scorer(
    input: Any = None, output: Any = None, expected: Any = None, metadata: Any = None, trace: Any = None, **_: Any
) -> dict[str, Any] | None:
    retry, drop = grounding_retry_rate(_run_for(output, metadata, trace))
    return _score("grounding_retry_rate", retry.value, drop_rate=drop.value, dropped=drop.detail)


def budget_exceeded_scorer(
    input: Any = None, output: Any = None, expected: Any = None, metadata: Any = None, trace: Any = None, **_: Any
) -> dict[str, Any] | None:
    return _score("budget_exceeded", budget_exceeded(_run_for(output, metadata, trace)))


def wrong_auto_accept_scorer(
    input: Any = None, output: Any = None, expected: Any = None, metadata: Any = None, **_: Any
) -> dict[str, Any] | None:
    """On a review span: ``output`` holds the spot check (call id, auto-accepted ids, wrong ids); not a spot check: None."""
    if not isinstance(output, dict) or "auto_accepted" not in output or "wrong" not in output:
        return None
    check = SpotCheck.model_validate({k: v for k, v in output.items() if k in SpotCheck.model_fields})
    return _score("wrong_auto_accept", wrong_auto_accept_score(check), wrong=check.wrong)


ONLINE_SCORERS: dict[str, Callable[..., dict[str, Any] | None]] = {
    "policy": policy_scorer,
    "schema_valid_rate": schema_valid_scorer,
    "grounding_retry_rate": grounding_retry_scorer,
    "budget_exceeded": budget_exceeded_scorer,
    "wrong_auto_accept": wrong_auto_accept_scorer,
}


def register_online_scorers(project: str | None = None, *, projects: Any = None) -> list[Any]:
    """Define the online scorers as Braintrust code functions in the project that receives production traces
    (``settings.braintrust_project``); ``braintrust push`` uploads them and the project's online scoring rules run
    them on every trace."""
    if projects is None:
        import braintrust

        projects = braintrust.projects
    if project is None:
        from ..config import get_settings

        project = get_settings().braintrust_project
    p = projects.create(name=project)
    return [
        p.scorers.create(
            name=name,
            slug=f"advisor-intel-{name.replace('_', '-')}",
            description=(fn.__doc__ or f"{name} (spec §13.4, §14.4)").strip().splitlines()[0],
            handler=fn,
            parameters=OnlineScorerArgs,
            if_exists="replace",
        )
        for name, fn in ONLINE_SCORERS.items()
    ]
