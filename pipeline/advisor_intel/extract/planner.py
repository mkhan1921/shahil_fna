"""The planner (spec §7.1): what to extract, and in which order.

The planner agent (frontier model, ``NativeOutput``) proposes an ordering and a rationale from the
topic-index counts, the registry, the quality report and the tier A slots; code then enforces the
rules, so a weak or missing proposal can never drop work:

- one task per (slot group × person) for per-person groups, one per group otherwise (list groups
  find their objects in stage 1); a window over ~25k tokens is split by time;
- dependencies come from the catalog's ``depends_on`` (retirement age needs the date of birth);
- every tier A slot is in some task, even with no indexed segments — such a task gets a window
  from a full-transcript keyword scan, or, when nothing matches, confirms ``not_discussed`` without
  a model call;
- ``segment_ids`` = the group's indexed segments, two segments of context each side, and every
  segment flagged as a revision of those.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, WithJsonSchema
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rank_bm25 import BM25Okapi

from .. import llm
from ..models.entities import DiscourseOutput
from ..models.extraction import ExtractionTask, Plan
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..prompts import pinned
from ..schema.slots import Catalog, generic_slot_id

CONTEXT_EACH_SIDE = 2
MAX_WINDOW_TOKENS = 25_000
KEYWORD_TOP_K = 8


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 1]


def group_is_per_person(catalog: Catalog, group: str) -> bool:
    return any("{p}" in s.slot_id for s in catalog.in_group(group))


def persons(profile: Profile | None, discourse: DiscourseOutput | None) -> list[str]:
    ents = {**(profile.entities if profile else {}), **(discourse.entities if discourse else {})}
    out = ["client"]
    if "person.spouse" in ents:
        out.append("spouse")
    return out


def _with_context(order: list[str], picked: set[str], each_side: int = CONTEXT_EACH_SIDE) -> list[str]:
    pos = {sid: i for i, sid in enumerate(order)}
    keep: set[str] = set()
    for sid in picked:
        if sid not in pos:
            continue
        i = pos[sid]
        for j in range(max(0, i - each_side), min(len(order), i + each_side + 1)):
            keep.add(order[j])
    return sorted(keep, key=lambda s: pos[s])


def keyword_scan(transcript: Transcript, catalog: Catalog, group: str, k: int = KEYWORD_TOP_K) -> list[str]:
    """BM25 over the whole transcript with the group's slot labels and questions as the query."""
    segs = transcript.segments
    if not segs:
        return []
    corpus = [_tokens(s.text) or ["_"] for s in segs]
    bm = BM25Okapi(corpus)
    query = _tokens(" ".join(f"{s.label} {s.question}" for s in catalog.in_group(group)) + " " + catalog.groups[group].description)
    scores = bm.get_scores(query)
    ranked = sorted(range(len(segs)), key=lambda i: -scores[i])
    return [segs[i].segment_id for i in ranked[:k] if scores[i] > 0]


def _window_tokens(transcript: Transcript, ids: list[str]) -> int:
    idx = transcript.index()
    return sum(len(idx[i].text) for i in ids if i in idx) // 4


def _split_by_time(ids: list[str], transcript: Transcript) -> list[list[str]]:
    if _window_tokens(transcript, ids) <= MAX_WINDOW_TOKENS:
        return [ids]
    out: list[list[str]] = [[]]
    idx = transcript.index()
    budget = 0
    for sid in ids:
        n = len(idx[sid].text) // 4 if sid in idx else 0
        if budget + n > MAX_WINDOW_TOKENS and out[-1]:
            out.append([])
            budget = 0
        out[-1].append(sid)
        budget += n
    return out


class PlannedGroup(BaseModel):
    slot_group: str
    priority: int = Field(description="1 runs first")
    reason: str


class PlanProposal(BaseModel):
    order: list[PlannedGroup]
    rationale: str


planner_agent: Agent[None, PlanProposal] = Agent(None, output_type=str, name="planner")


def _proposal_model(groups: list[str]) -> type[BaseModel]:
    enum = Annotated[Literal[tuple(groups)], WithJsonSchema({"type": "string", "enum": list(groups)})]  # type: ignore[valid-type]

    class Group(BaseModel):
        slot_group: enum  # type: ignore[valid-type]
        priority: int = Field(description="1 runs first")
        reason: str

    class Proposal(BaseModel):
        order: list[Group]
        rationale: str

    return Proposal


def _brief(catalog: Catalog, discourse: DiscourseOutput, transcript: Transcript, profile: Profile | None) -> str:
    lines = ["## Slot groups (indexed segments, tier A slots)"]
    for g, grp in catalog.groups.items():
        tier_a = [s.slot_id for s in catalog.in_group(g) if s.sensitivity_tier == "A"]
        lines.append(f"- {g}: {len(discourse.topic_index.get(g, []))} segments; tier A: {', '.join(tier_a) or 'none'} — {grp.description}")
    lines.append(f"\n## Registry\n{len(discourse.entities)} entities: " + ", ".join(list(discourse.entities)[:40]))
    if transcript.quality:
        q = transcript.quality
        lines.append(f"\n## Quality\nmean word confidence {q.mean_word_conf:.2f}; low-ASR share {q.low_asr_share:.0%}; speakers {q.n_speakers}")
    if profile is not None:
        priors = sum(1 for f in profile.facts.values() if f.source == "prior_fna")
        lines.append(f"\n## Prior FNA\n{priors} fields from the client's existing document")
    return "\n".join(lines)


async def propose(catalog: Catalog, discourse: DiscourseOutput, transcript: Transcript, profile: Profile | None, pins: VersionPins) -> tuple[dict[str, int], str, dict[str, int]]:
    """The model's proposed group priorities and rationale (empty for stub pins)."""
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    rm = llm.for_role("frontier", pins)
    if llm.is_stub(rm.name):
        return {}, "", usage
    prompt = pinned("planner", pins.prompt_versions)
    out_model = _proposal_model(catalog.group_names())
    try:
        result = await planner_agent.run(_brief(catalog, discourse, transcript, profile), model=rm.model, model_settings=rm.settings,
                                         instructions=prompt.text, output_type=NativeOutput(out_model))
    except UnexpectedModelBehavior as e:
        # The proposal only orders work within a tier; the enforced plan does not depend on it.
        usage["requests"] = 1
        return {}, f"Deterministic plan (the planner's proposal was unusable: {str(e)[:120]}).", usage
    usage.update(requests=result.usage.requests, input_tokens=result.usage.input_tokens or 0, output_tokens=result.usage.output_tokens or 0)
    pri = {g.slot_group: max(1, int(g.priority)) for g in result.output.order}  # type: ignore[attr-defined]
    return pri, result.output.rationale, usage  # type: ignore[attr-defined]


def build_plan(
    catalog: Catalog,
    discourse: DiscourseOutput,
    transcript: Transcript,
    profile: Profile | None = None,
    *,
    proposed_priority: dict[str, int] | None = None,
    rationale: str = "",
) -> Plan:
    """The enforced plan (§7.1 rules), using the model's priorities only to order within a tier."""
    order = [s.segment_id for s in transcript.segments]
    revisions_of: dict[str, set[str]] = {}
    for r in discourse.revisions:
        revisions_of.setdefault(r.revises_segment_id, set()).add(r.segment_id)
    people = persons(profile, discourse)
    tasks: list[ExtractionTask] = []
    by_group: dict[str, list[str]] = {}
    for g in catalog.groups:
        slots = catalog.in_group(g)
        if not slots:
            continue
        indexed = set(discourse.topic_index.get(g, []))
        has_tier_a = any(s.sensitivity_tier == "A" for s in slots)
        skip_reason = None
        if not indexed:
            indexed = set(keyword_scan(transcript, catalog, g)) if has_tier_a else set()
            if not indexed and not has_tier_a:
                skip_reason = "no indexed segments and no tier A slots"
        for sid in list(indexed):
            indexed |= revisions_of.get(sid, set())
        window = _with_context(order, indexed)
        tier = min({"A": 1, "B": 2, "C": 3}[s.sensitivity_tier] for s in slots)
        priority = tier * 10 + (proposed_priority or {}).get(g, 5)
        instances: list[str | None] = list(people) if group_is_per_person(catalog, g) else [None]
        for inst in instances:
            for part, ids in enumerate(_split_by_time(window, transcript) if window else [[]]):
                suffix = f":{inst}" if inst else ""
                tid = f"{g}{suffix}" + (f":{part + 1}" if part else "")
                slot_ids = [s.expand(inst) if inst and s.per_person else s.slot_id for s in slots]
                tasks.append(
                    ExtractionTask(
                        task_id=tid,
                        slot_group=g,
                        slot_ids=slot_ids,
                        instance_keys=[inst],
                        segment_ids=ids,
                        priority=priority,
                        status="skipped" if skip_reason else "todo",
                        skip_reason=skip_reason,
                    )
                )
                by_group.setdefault(g, []).append(tid)
    # Dependency edges from the catalog: a task waits for the tasks holding the slots it depends on.
    for t in tasks:
        inst = t.instance_keys[0] if t.instance_keys else None
        deps: set[str] = set()
        for sid in t.slot_ids:
            for dep in catalog.slot(sid).depends_on:
                concrete = dep.replace("{p}", inst) if inst else dep
                for other in tasks:
                    if other.slot_group != t.slot_group and concrete in other.slot_ids:
                        deps.add(other.task_id)
        t.depends_on = sorted(deps)
    tasks = _topological(tasks)
    missing = [s.slot_id for s in catalog.tier_a() if not any(s.slot_id == generic_slot_id(x) or s.slot_id == x for t in tasks for x in t.slot_ids)]
    if missing:  # pragma: no cover - every catalog slot belongs to a group, so this is a catalog error
        raise ValueError(f"tier A slots not in any task: {missing}")
    return Plan(tasks=tasks, rationale=rationale or "Deterministic plan: dependencies first, then tier A groups, then the rest.")


def _topological(tasks: list[ExtractionTask]) -> list[ExtractionTask]:
    """Order by priority, never before a dependency."""
    remaining = sorted(tasks, key=lambda t: (t.priority, t.task_id))
    done: set[str] = set()
    out: list[ExtractionTask] = []
    while remaining:
        for i, t in enumerate(remaining):
            if all(d in done for d in t.depends_on):
                out.append(t)
                done.add(t.task_id)
                remaining.pop(i)
                break
        else:  # a cycle: drop the offending edges rather than stall
            t = remaining.pop(0)
            t.depends_on = [d for d in t.depends_on if d in done]
            out.append(t)
            done.add(t.task_id)
    return out


async def make_plan(catalog: Catalog, discourse: DiscourseOutput, transcript: Transcript, profile: Profile | None, pins: VersionPins) -> tuple[Plan, dict[str, int]]:
    pri, rationale, usage = await propose(catalog, discourse, transcript, profile, pins)
    return build_plan(catalog, discourse, transcript, profile, proposed_priority=pri, rationale=rationale), usage
