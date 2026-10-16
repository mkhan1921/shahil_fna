"""The orchestrator's tools, in three toolsets (spec §13.3).

Arguments are typed, so invalid arguments go back to the model as a retry with the validation
message. Every tool runs behind a guard: identical calls are blocked on the third repeat within
ten calls (§13.7), errors count against a per-tool budget of five after which the tool is removed
from the run, and results are capped (6,000 characters; ``get_segments`` 20,000) with a note on how
to page. The agent may only send RETRACT, MARK_UNKNOWN and CONFIRM through ``apply_state_ops``.
"""

from __future__ import annotations

import functools
import json
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry, ToolFailed
from pydantic_ai.toolsets import FunctionToolset
from rank_bm25 import BM25Okapi

from ..binding import describe_value
from ..discourse.text import render_segment
from ..embeddings import cosine, get_embedder
from ..extract import planner
from ..extract.context import concrete_path
from ..extract.reducer import make_ops
from ..models.extraction import ExtractionTask
from ..models.facts import Provenance
from ..schema.slots import fact_id_for
from .deps import HarnessDeps
from .loop import args_hash
from .state import ToolEvent

RESULT_CAP = 6_000
SEGMENTS_CAP = 20_000
MAX_SEGMENTS = 40
ERROR_BUDGET = 5


def _cap(result: Any, cap: int) -> Any:
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    if len(text) <= cap:
        return result
    return text[: cap - 200] + f"\n…[truncated at {cap} characters: ask for fewer items, a narrower slot group, or page with smaller id lists]"


def guarded(name: str, cap: int = RESULT_CAP):
    """Loop detection, the error budget and the result cap around one tool (§13.3, §13.7)."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(ctx: RunContext[HarnessDeps], *args: Any, **kwargs: Any) -> Any:
            deps = ctx.deps
            st = deps.state
            if name in st.disabled_tools:
                raise ToolFailed(f"{name} has failed {ERROR_BUDGET} times and is no longer available in this run.")
            call_args = dict(kwargs)
            if not deps.loop.observe(name, call_args):
                st.events.append(ToolEvent(turn=st.turn, tool=name, args_hash=args_hash(name, call_args), ok=False, note="repeated"))
                return f"Error: you have called {name} with the same arguments three times in your last ten calls. Change approach: search the transcript, update the plan, or mark the slot unknown."
            try:
                result = await fn(ctx, *args, **kwargs)
            except ModelRetry:
                _count_error(deps, name)
                st.events.append(ToolEvent(turn=st.turn, tool=name, args_hash=args_hash(name, call_args), ok=False, note="retry"))
                raise
            except ToolFailed:
                _count_error(deps, name)
                st.events.append(ToolEvent(turn=st.turn, tool=name, args_hash=args_hash(name, call_args), ok=False, note="failed"))
                raise
            except Exception as e:  # a bug or provider error inside the tool: report it, don't crash the run
                _count_error(deps, name)
                st.events.append(ToolEvent(turn=st.turn, tool=name, args_hash=args_hash(name, call_args), ok=False, note=type(e).__name__))
                raise ToolFailed(f"{name} failed: {type(e).__name__}: {str(e)[:300]}") from e
            st.events.append(ToolEvent(turn=st.turn, tool=name, args_hash=args_hash(name, call_args), ok=True))
            return _cap(result, cap)

        return wrapper

    return deco


def _count_error(deps: HarnessDeps, name: str) -> None:
    st = deps.state
    st.tool_errors[name] = st.tool_errors.get(name, 0) + 1
    if st.tool_errors[name] >= ERROR_BUDGET and name not in st.disabled_tools:
        st.disabled_tools.append(name)


def _enabled(ctx: RunContext[HarnessDeps], tool_def: Any) -> bool:
    return tool_def.name not in ctx.deps.state.disabled_tools


# --------------------------------------------------------------------------- planning tools

planning = FunctionToolset[HarnessDeps](max_retries=ERROR_BUDGET)


def plan_view(deps: HarnessDeps) -> dict[str, Any]:
    plan = deps.state.plan
    if plan is None:
        return {"tasks": [], "rationale": "no plan yet: call make_plan"}
    return {
        "rationale": plan.rationale,
        "tasks": [
            {
                "task_id": t.task_id,
                "slot_group": t.slot_group,
                "instance": t.instance_keys[0] if t.instance_keys else None,
                "segments": len(t.segment_ids),
                "depends_on": t.depends_on,
                "priority": t.priority,
                "status": t.status,
                "skip_reason": t.skip_reason,
                "attempts": t.attempts,
            }
            for t in plan.tasks
        ],
    }


@planning.tool
@guarded("make_plan")
async def make_plan(ctx: RunContext[HarnessDeps]) -> dict[str, Any]:
    """Plan the extraction: one task per slot group and person, tier A first, dependencies respected. Call this first."""
    d = ctx.deps
    plan, usage = await planner.make_plan(d.catalog, d.discourse, d.transcript, d.profile, d.pins)
    d.add_usage(usage, role="frontier")
    if d.state.plan is not None:  # re-planning keeps the progress already made
        done = {t.task_id: t for t in d.state.plan.tasks}
        for t in plan.tasks:
            if t.task_id in done and done[t.task_id].status in ("done", "failed", "skipped"):
                t.status, t.skip_reason, t.attempts = done[t.task_id].status, done[t.task_id].skip_reason, done[t.task_id].attempts
    d.state.plan = plan
    return plan_view(d)


@planning.tool
@guarded("view_plan")
async def view_plan(ctx: RunContext[HarnessDeps]) -> dict[str, Any]:
    """The task list with status: your working memory."""
    return plan_view(ctx.deps)


@planning.tool
@guarded("update_plan")
async def update_plan(
    ctx: RunContext[HarnessDeps],
    action: Literal["add", "split", "skip"],
    task_id: str,
    reason: str,
    slot_group: str | None = None,
    instance: str | None = None,
    segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Add a task (a slot group, optional person, and the segments to read), split a task in two by time, or skip a task with a reason the reviewer will see."""
    d = ctx.deps
    plan = d.state.plan
    if plan is None:
        raise ModelRetry("There is no plan yet: call make_plan first.")
    if not reason.strip():
        raise ModelRetry("Give a reason; skipping a tier A task shows it to the reviewer.")
    if action == "add":
        if slot_group not in d.catalog.groups:
            raise ModelRetry(f"Unknown slot group {slot_group!r}; valid: {', '.join(d.catalog.groups)}")
        slots = d.catalog.in_group(slot_group)
        ids = [s for s in (segment_ids or []) if s in d.transcript.index()]
        new = ExtractionTask(task_id=task_id, slot_group=slot_group, slot_ids=[s.expand(instance) if instance and s.per_person else s.slot_id for s in slots],
                             instance_keys=[instance], segment_ids=ids, priority=1)
        if any(t.task_id == task_id for t in plan.tasks):
            raise ModelRetry(f"Task {task_id} already exists; pick a new id.")
        plan.tasks.append(new)
        d.state.notes.append(f"added task {task_id}: {reason}")
        return plan_view(d)
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None:
        raise ModelRetry(f"No task {task_id}; call view_plan.")
    if action == "skip":
        task.status, task.skip_reason = "skipped", reason
        return plan_view(d)
    half = len(task.segment_ids) // 2
    if half < 1:
        raise ModelRetry("This task's window is too small to split.")
    second = task.model_copy(update={"task_id": f"{task.task_id}:b", "segment_ids": task.segment_ids[half:], "status": "todo", "attempts": 0})
    task.segment_ids = task.segment_ids[:half]
    plan.tasks.insert(plan.tasks.index(task) + 1, second)
    return plan_view(d)


@planning.tool
@guarded("view_profile")
async def view_profile(
    ctx: RunContext[HarnessDeps],
    slot_group: str | None = None,
    only: list[Literal["gaps", "low_conf", "conflicts"]] | None = None,
) -> list[dict[str, Any]]:
    """A compact fact table (no evidence text). Filter by slot group, and by gaps (tier A not discussed), low confidence (p < 0.6) or conflicts (contradicted)."""
    d = ctx.deps
    rows = []
    for f in sorted(d.profile.facts.values(), key=lambda x: x.fact_id):
        slot = d.catalog.slot(f.slot_id)
        if slot_group and slot.group != slot_group:
            continue
        if only:
            keep = ("gaps" in only and slot.sensitivity_tier == "A" and f.status in ("not_discussed", "explicitly_unknown")) or (
                "low_conf" in only and f.p_correct < 0.6 and f.source == "call") or ("conflicts" in only and f.status == "contradicted")
            if not keep:
                continue
        rows.append({"fact_id": f.fact_id, "status": f.status, "value": describe_value(f.value), "p": round(f.p_correct, 2), "source": f.source,
                     "attribution": f.speaker_attribution, "tier": slot.sensitivity_tier})
    return rows


@planning.tool
@guarded("note")
async def note(ctx: RunContext[HarnessDeps], text: str) -> str:
    """Record something you will need later; notes survive context compaction."""
    ctx.deps.state.notes.append(text.strip()[:1000])
    return "noted"


# --------------------------------------------------------------------------- retrieval tools

retrieval = FunctionToolset[HarnessDeps](max_retries=ERROR_BUDGET)


@retrieval.tool
@guarded("get_segments", cap=SEGMENTS_CAP)
async def get_segments(ctx: RunContext[HarnessDeps], segment_ids: list[str]) -> str:
    """Up to 40 transcript segments, formatted as [id | speaker | time] text ⟨rewrite⟩."""
    d = ctx.deps
    if len(segment_ids) > MAX_SEGMENTS:
        raise ModelRetry(f"Ask for at most {MAX_SEGMENTS} segments at a time.")
    idx = d.transcript.index()
    unknown = [s for s in segment_ids if s not in idx]
    lines = [render_segment(idx[s], rewrite=d.discourse.rewrites.get(s)) for s in segment_ids if s in idx]
    if unknown:
        lines.append(f"(unknown segment ids: {', '.join(unknown[:10])})")
    return "\n".join(lines) or "(no segments)"


def _rrf(*rankings: list[str], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for r, sid in enumerate(ranking):
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + r + 1)
    return sorted(scores, key=lambda s: -scores[s])


@retrieval.tool
@guarded("search_transcript")
async def search_transcript(ctx: RunContext[HarnessDeps], query: str, mode: Literal["keyword", "semantic"] | None = None, k: int = 10) -> list[dict[str, Any]]:
    """Search the call: keyword (BM25), semantic (embeddings), or both merged when mode is not set. Returns segment ids with snippets."""
    d = ctx.deps
    k = max(1, min(20, k))
    segs = d.transcript.segments
    if not segs:
        return []
    keyword: list[str] = []
    semantic: list[str] = []
    if mode in (None, "keyword"):
        tok = lambda s: [w for w in "".join(c.lower() if c.isalnum() else " " for c in s).split() if w]
        bm = BM25Okapi([tok(s.text + " " + d.discourse.rewrites.get(s.segment_id, "")) or ["_"] for s in segs])
        sc = bm.get_scores(tok(query))
        keyword = [segs[i].segment_id for i in np.argsort(-sc) if sc[i] > 0][: k * 2]
    if mode in (None, "semantic"):
        emb = get_embedder(d.pins.embedding_model)
        vecs = d.discourse.segment_vectors
        mat = np.asarray([vecs[s.segment_id] for s in segs], dtype=np.float32) if all(s.segment_id in vecs for s in segs) else emb.embed_documents([s.text for s in segs])
        sims = cosine(emb.embed_queries([query]), mat)[0]
        semantic = [segs[i].segment_id for i in np.argsort(-sims)][: k * 2]
    ranked = _rrf(keyword, semantic) if mode is None else (keyword or semantic)
    idx = d.transcript.index()
    return [{"segment_id": sid, "speaker": idx[sid].speaker_role, "snippet": idx[sid].text[:200]} for sid in ranked[:k]]


@retrieval.tool
@guarded("get_entity")
async def get_entity(ctx: RunContext[HarnessDeps], entity_id: str) -> dict[str, Any]:
    """An entity from the registry with the segments that mention it."""
    d = ctx.deps
    e = d.entities.get(entity_id) or d.discourse.entities.get(entity_id)
    if e is None:
        raise ModelRetry(f"No entity {entity_id!r}. Known: {', '.join(list(d.entities)[:30])}")
    mentions = [{"segment_id": m.segment_id, "surface": m.surface} for m in d.discourse.mentions if m.entity_id == entity_id][:40]
    return {**e.model_dump(mode="json"), "mentions": mentions}


# --------------------------------------------------------------------------- work tools

work = FunctionToolset[HarnessDeps](max_retries=ERROR_BUDGET)


def extraction_key(task: ExtractionTask) -> str:
    """The unit the re-extraction budget counts: a slot group for one person (or the group alone)."""
    inst = task.instance_keys[0] if task.instance_keys and task.instance_keys[0] else None
    return f"{task.slot_group}#{inst}" if inst else task.slot_group


class AgentOp(BaseModel):
    """An op the orchestrator may apply directly (never a value: ADD and UPDATE come only from extraction)."""

    op: Literal["RETRACT", "MARK_UNKNOWN", "CONFIRM"]
    fact_id: str = Field(description="slot_id#instance, e.g. 'retirement.{p}.retirementAge#client'")
    reason: str
    segment_id: str | None = None


@work.tool
@guarded("extract_slot_group")
async def extract_slot_group(ctx: RunContext[HarnessDeps], task_id: str) -> dict[str, Any]:
    """Run the extraction sub-agent for one plan task and apply its ops through the reducer."""
    d = ctx.deps
    plan = d.state.plan
    if plan is None:
        raise ModelRetry("Call make_plan first.")
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None:
        raise ModelRetry(f"No task {task_id}; call view_plan.")
    unmet = [dep for dep in task.depends_on if next((t.status for t in plan.tasks if t.task_id == dep), "done") not in ("done", "skipped", "failed")]
    if unmet:
        raise ModelRetry(f"Task {task_id} depends on {', '.join(unmet)}: run those first.")
    # Re-extractions are budgeted per slot group and person (§13.6), whichever task asks: re-running a done
    # task, a task added with update_plan or a split half all count, so the budget cannot be bypassed.
    key = extraction_key(task)
    runs = d.state.extraction_attempts.get(key, 0)
    limit = 1 + d.inputs.budgets.reextractions_per_group
    if runs >= limit:
        task.status = task.status if task.status in ("done", "failed", "skipped") else "skipped"
        task.skip_reason = task.skip_reason or f"re-extraction budget spent ({runs} extractions of {key})"
        raise ToolFailed(
            f"{key} has been extracted {runs} times, the budget for this slot group; mark the remaining slots unknown (apply_state_ops) or skip the task."
        )
    task.status = "running"
    task.attempts += 1
    d.state.extraction_attempts[key] = runs + 1
    try:
        profile, result = await d.services.extract(task, d)
    except Exception:
        task.status = "failed"
        raise
    await d.set_profile(profile)
    d.add_usage(result.usage)
    task.status = "done"
    return {
        "task_id": task_id,
        "ops_applied": result.ops_applied,
        "new_fact_ids": result.new_fact_ids[:60],
        "tier_a_gaps": result.gaps,
        "warnings": result.warnings[:20],
        "dropped": result.dropped[:20],
    }


@work.tool
@guarded("verify_facts")
async def verify_facts(ctx: RunContext[HarnessDeps], slot_group: str | None = None, fact_ids: list[str] | None = None) -> dict[str, Any]:
    """Check a slot group's new facts (entailment, consistency) and apply the verifier's corrective ops."""
    d = ctx.deps
    if slot_group is not None and slot_group not in d.catalog.groups:
        raise ModelRetry(f"Unknown slot group {slot_group!r}")
    ids = fact_ids or [f.fact_id for f in d.profile.facts.values() if f.source == "call" and (slot_group is None or d.catalog.slot(f.slot_id).group == slot_group)]
    if slot_group and slot_group not in d.state.verified_groups:
        d.state.verified_groups.append(slot_group)
    if d.services.verify is None:
        return {"verified": 0, "note": "verification is not configured in this run; facts keep their extraction scores"}
    res = await d.services.verify(d.profile, ids, d)
    if res.profile is not None:
        await d.set_profile(res.profile)
    d.add_usage(res.usage, role="verifier")
    return res.data if isinstance(res.data, dict) else {"result": res.data}


@work.tool
@guarded("check_constraints")
async def check_constraints(ctx: RunContext[HarnessDeps]) -> list[dict[str, Any]]:
    """Code rules over the whole profile (ranges, ordering, budget, coherence). Hard failures go to review."""
    d = ctx.deps
    if d.services.constraints is None:
        return []
    res = await d.services.constraints(d.profile, d)
    if res.profile is not None:
        await d.set_profile(res.profile)
    d.state.constraint_violations = list(res.data or [])
    return d.state.constraint_violations


@work.tool
@guarded("apply_state_ops")
async def apply_state_ops(ctx: RunContext[HarnessDeps], ops: list[AgentOp]) -> dict[str, Any]:
    """Retract a fact, mark a slot explicitly unknown, or confirm a fact — never add or change a value."""
    d = ctx.deps
    specs = []
    for o in ops:
        slot_id, _, instance = o.fact_id.partition("#")
        try:
            slot = d.catalog.slot(slot_id)
        except KeyError as e:
            raise ModelRetry(f"Unknown slot in fact id {o.fact_id!r}") from e
        if o.op == "MARK_UNKNOWN":
            payload = {"reason": o.reason, "slot_id": slot.slot_id, "instance_key": instance or None,
                       "fna_path": concrete_path(d.profile, slot, instance or None),
                       "provenance": Provenance(run_id=d.inputs.run_id, schema_version=d.catalog.version)}
        elif o.op == "RETRACT":
            payload = {"reason": "change_of_mind" if "mind" in o.reason.lower() else "extraction_error", "note": o.reason}
        else:
            payload = {}
        specs.append((o.op, fact_id_for(slot.slot_id, instance or None), o.segment_id, payload, "extractor"))
    result = await d.apply(make_ops(d.profile, specs))
    return {"applied": len(result.applied), "rejected": [{"fact_id": r.op.fact_id, "reason": r.reason} for r in result.rejected]}


@work.tool
@guarded("run_sensitivity")
async def run_sensitivity(ctx: RunContext[HarnessDeps]) -> list[dict[str, Any]]:
    """Rank facts by value of information (forecast and FNA sensitivity) and set review priorities."""
    d = ctx.deps
    if d.services.sensitivity is None:
        return []
    res = await d.services.sensitivity(d.profile, d)
    if res.profile is not None:
        await d.set_profile(res.profile)
    d.state.sensitivity = list(res.data or [])
    return d.state.sensitivity[:30]


@work.tool
@guarded("draft_followups")
async def draft_followups(ctx: RunContext[HarnessDeps]) -> list[dict[str, Any]]:
    """Draft client follow-up questions for the facts that need the client (at most 8)."""
    d = ctx.deps
    if d.services.followups is None:
        return []
    res = await d.services.followups(d.profile, d)
    if res.profile is not None:
        await d.set_profile(res.profile)
    d.add_usage(res.usage, role="frontier")
    d.state.followups = list(res.data or [])
    return [f.model_dump(mode="json") for f in d.state.followups]


@work.tool
@guarded("lookup_term")
async def lookup_term(ctx: RunContext[HarnessDeps], term: str, context: str = "") -> dict[str, Any]:
    """Resolve an unknown product, provider or scheme name: practice glossary, product catalogue, then allow-listed web search."""
    d = ctx.deps
    if d.services.lookup_term is None:
        return {"term": term, "resolved": False, "candidates": [], "note": "term lookup is not configured in this run"}
    res = await d.services.lookup_term(term, context, d)
    d.add_usage(res.usage, role="small")
    return res.data if isinstance(res.data, dict) else {"result": res.data}


TOOLSETS = [planning.filtered(_enabled), retrieval.filtered(_enabled), work.filtered(_enabled)]
TOOL_NAMES = [
    "make_plan", "view_plan", "update_plan", "view_profile", "note",
    "get_segments", "search_transcript", "get_entity",
    "extract_slot_group", "verify_facts", "check_constraints", "apply_state_ops", "run_sensitivity", "draft_followups", "lookup_term",
]
