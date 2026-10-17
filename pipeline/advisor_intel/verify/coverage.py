"""Omission check — ``check_coverage(profile)`` (spec §9.1d).

An open "is anything missing?" question performs close to chance, so code lists what should be there
and asks a closed question about each item still ``not_discussed``. The expected-fact list:

- every tier A slot for each person in the household (and the household-level tier A slots), with the
  conditional ones applied: the retirement target the target mode needs, the marital regime only when
  married, a guardian only when there are children;
- every attribute the FNA engine needs for each entity found: a dependant's date of birth, relationship
  and education plan; a policy's type, cover (or monthly benefit), life assured and beneficiary; a fund's
  type, value and contributions; an asset's type and value; a debt's type, balance and repayment; a
  goal's cost and target year;
- every non-empty field of the prior FnaDocument, to tell "updated" and "confirmed" from "not mentioned";
- every slot group the topic index tagged segments for but that produced no fact.

The verifier model (a different family from extraction, §9.1e) gets the top retrieved segments and one
closed question per item — "Does this conversation state or imply <field> for <entity>? Answer present
or absent, with the segment id" — batched 20 per call. A ``present`` answer reopens the field through the
injected ``reopen(slot_id, instance, segment_ids)``, which runs one targeted value extraction (§7.2 stage
3) and alignment (§7.3) and returns state ops; they go through the reducer. If the extractor still finds
nothing, the question is asked again; when the omission check and the extractor disagree twice, the
field becomes ``contradicted`` for review.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

from .. import stats
from ..embeddings import Embedder, cosine, get_embedder
from ..extract.reducer import RejectedOp, annotate, apply_state_ops, make_ops
from ..models.facts import Evidence, Hypothesis, Provenance, Quantity
from ..models.profile import Profile, StateOp
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.slots import PERSONS, Catalog, SlotDef, fact_id_for, load_catalog
from .agents import OFFLINE, Resolved, Usage, format_segment, is_offline, resolve, run_guarded
from .claims import noun, subject

log = logging.getLogger(__name__)

BATCH = 20  # §9.1d: questions are batched 20 per call
TOP_SEGMENTS = 6
MAX_DISAGREEMENTS = 2
Reason = Literal["tier_a", "entity_attribute", "prior_field", "topic_index"]
Reopen = Callable[[str, "str | None", list[str]], Awaitable[list[StateOp]]]
OnProfile = Callable[[Profile], None]  # told every time the check changes the profile (a live view for the reopen callable)
OFFLINE_SKIP = "the verifier role is a stub: the omission check did not run"
UNPARTNERED = ("single", "divorced", "widowed")
CONTRIBUTING_FUNDS = ("pension", "provident", "retirement-annuity")
MONTHLY_BENEFIT_POLICIES = ("income-protection", "family-income")
BENEFICIARY_POLICIES = ("life", "family-income", "funeral")

# The list each catalog list entity lives in, and each registry list's catalog entity.
ENTITY_PREFIX: dict[str, str] = {
    "dependant": "dependants[]",
    "fund": "retirementFunds[]",
    "asset": "assets[]",
    "liability": "liabilities[]",
    "policy": "policies[]",
    "goal": "goals[]",
    "expense": "expenses.custom[]",
    "event": "event[]",
}
FNA_LIST_ENTITY: dict[str, str] = {
    "dependants": "dependant",
    "retirementFunds": "fund",
    "assets": "asset",
    "liabilities": "liability",
    "policies": "policy",
    "goals": "goal",
    "expenses.custom": "expense",
}
# The slot asked about when a list group produced no row at all.
GROUP_IDENTITY: dict[str, str] = {
    "dependants": "dependants[].relationship",
    "education": "dependants[].tertiary",
    "retirement_funds": "retirementFunds[].type",
    "assets": "assets[].type",
    "liabilities": "liabilities[].type",
    "policies": "policies[].type",
    "policy_terms": "policies[].type",
    "goals": "goals[].name",
    "expenses": "expenses.items[]",
    "events": "event[].kind",
}


class ExpectedFact(BaseModel):
    fact_id: str  # the fact the item would fill; a bare slot id for "any row of this list"
    slot_id: str
    instance: str | None
    reasons: list[Reason]
    tier: str
    group: str
    label: str  # "the client's target retirement age"


# --------------------------------------------------------------------------- the expected-fact list


def _value(profile: Profile, fid: str) -> Any:
    f = profile.facts.get(fid)
    if f is None or f.value is None or f.review.status == "rejected":
        return None
    d = f.value.dist
    try:
        if stats.is_boolean(d):
            return stats.p_true(d) >= 0.5
        if stats.is_categorical(d):
            return stats.mode_label(d)
        return stats.median(d)
    except (TypeError, ValueError):
        return None


def household_persons(profile: Profile) -> list[str]:
    status = _value(profile, "household.maritalStatus")
    spouse = (
        "person.spouse" in profile.entities or _value(profile, "household.includeSpouse") is True or status in ("married", "life-partner")
    )
    spouse = spouse or any(fid.endswith("#spouse") for fid in profile.facts)
    return list(PERSONS) if spouse else ["client"]


def found_entities(profile: Profile, catalog: Catalog) -> dict[str, str]:
    """Entity id → catalog list entity (dependant, fund, …) for every list row the call or the prior holds."""
    out: dict[str, str] = {}
    for e in profile.entities.values():
        kind = FNA_LIST_ENTITY.get(e.fna_list or "")
        if kind:
            out[e.entity_id] = kind
    for f in profile.facts.values():
        if not f.instance_key or f.review.status == "rejected":
            continue
        try:
            slot = catalog.slot(f.slot_id)
        except KeyError:
            continue
        if slot.entity_type in ENTITY_PREFIX and slot.slot_id.startswith(ENTITY_PREFIX[slot.entity_type]):
            out.setdefault(f.instance_key, slot.entity_type)
    return out


def _children(profile: Profile, entities: dict[str, str]) -> bool:
    """True when any dependant is (or may be) a child, so a guardian nomination matters."""
    for eid, kind in entities.items():
        if kind != "dependant":
            continue
        rel = _value(profile, fact_id_for("dependants[].relationship", eid))
        if rel is None and eid in profile.entities:
            rel = profile.entities[eid].attributes.get("relation")
        if rel in (None, "child", "daughter", "son"):
            return True
    return False


def entity_required(profile: Profile, entity_id: str, kind: str) -> list[str]:
    """The attributes the FNA engine needs for one entity (§9.1d)."""
    v = lambda slot_id: _value(profile, fact_id_for(slot_id, entity_id))
    if kind == "dependant":
        out = ["dependants[].dateOfBirth", "dependants[].relationship"]
        if v("dependants[].relationship") in (None, "child"):
            out.append("dependants[].tertiary")
            if v("dependants[].tertiary") is not False:
                out += ["dependants[].tertiaryStartAge", "dependants[].tertiaryCostAnnual"]
        return out
    if kind == "policy":
        ptype = v("policies[].type")
        out = ["policies[].type", "policies[].lifeAssured"]
        out.append("policies[].monthlyBenefit" if ptype in MONTHLY_BENEFIT_POLICIES else "policies[].cover")
        if ptype in (None, *BENEFICIARY_POLICIES):
            out.append("policies[].beneficiary")
        return out
    if kind == "fund":
        ftype = v("retirementFunds[].type")
        out = ["retirementFunds[].type", "retirementFunds[].value"]
        if ftype == "living-annuity":
            out.append("retirementFunds[].drawdownRate")
        elif ftype in (None, *CONTRIBUTING_FUNDS):  # preservation funds and annuities take no contributions
            out.append("retirementFunds[].employeeMonthly")
        return out
    if kind == "asset":
        return ["assets[].type", "assets[].value"]
    if kind == "liability":
        return ["liabilities[].type", "liabilities[].balance", "liabilities[].monthlyRepayment"]
    if kind == "goal":
        return ["goals[].cost", "goals[].targetYear"]
    return []


def _label(profile: Profile, catalog: Catalog, slot: SlotDef, instance: str | None) -> str:
    from ..models.facts import Fact

    probe = Fact(
        fact_id=fact_id_for(slot.slot_id, instance),
        slot_id=slot.slot_id,
        instance_key=instance,
        fna_path=slot.fna_path_for(instance if slot.per_person else None),
        status="not_discussed",
        provenance=Provenance(run_id=profile.run_id),
    )
    s, sp = subject(probe, profile, catalog)
    if slot.slot_id.startswith("riskProfile.answers["):
        qid = slot.slot_id[len("riskProfile.answers[") : -1]
        rq = next((r for r in catalog.risk_questions if r.id == qid), None)
        return f"the client's answer to the risk question '{rq.question if rq else qid}'"
    if slot.slot_id == "expenses.items[]":
        return f"{s}" if instance else "any monthly household expense (groceries, rent, school fees, insurance and so on)"
    if slot.per_entity:
        if instance is None:
            group = catalog.groups.get(slot.group)
            return f"any {group.description.rstrip('.').lower() if group else slot.group}"
        return f"the {noun(slot)} of {s}"
    return f"{sp} {noun(slot)}"


def expected_facts(
    profile: Profile, catalog: Catalog | None = None, *, topic_index: dict[str, list[str]] | None = None
) -> list[ExpectedFact]:
    """The expected-fact list for the call (§9.1d), deduplicated, in a stable order."""
    catalog = catalog or load_catalog(profile.schema_version)
    items: dict[str, ExpectedFact] = {}

    def want(slot_id: str, instance: str | None, reason: Reason) -> None:
        slot = catalog.slot(slot_id)
        fid = fact_id_for(slot.slot_id, instance)
        if fid in items:
            if reason not in items[fid].reasons:
                items[fid].reasons.append(reason)
            return
        items[fid] = ExpectedFact(
            fact_id=fid,
            slot_id=slot.slot_id,
            instance=instance,
            reasons=[reason],
            tier=slot.sensitivity_tier,
            group=slot.group,
            label=_label(profile, catalog, slot, instance),
        )

    entities = found_entities(profile, catalog)
    status = _value(profile, "household.maritalStatus")
    persons = household_persons(profile)
    # Tier A slots per person and for the household.
    for slot in catalog.tier_a():
        if not slot.exported:
            continue
        if slot.slot_id == "household.maritalRegime" and status not in (None, "married"):
            continue
        if slot.slot_id == "household.includeSpouse" and status in UNPARTNERED:
            continue
        if slot.per_person and not slot.per_entity:
            for p in persons:
                mode = _value(profile, fact_id_for("retirement.{p}.targetMode", p))
                if slot.slot_id == "retirement.{p}.targetPercent" and mode == "amount":
                    continue
                if slot.slot_id == "retirement.{p}.targetMonthly" and mode != "amount":
                    continue
                if slot.slot_id == "estate.{p}.guardianNominated" and not _children(profile, entities):
                    continue
                want(slot.slot_id, p, "tier_a")
        elif slot.cardinality == "one":
            want(slot.slot_id, None, "tier_a")
    # What the FNA engine needs for each entity found.
    for eid, kind in entities.items():
        for slot_id in entity_required(profile, eid, kind):
            want(slot_id, eid, "entity_attribute")
    # Every non-empty prior field.
    for f in profile.facts.values():
        if f.prior_value is not None and f.review.status != "rejected":
            want(f.slot_id, f.instance_key, "prior_field")
    # Topic-indexed groups that produced no fact.
    for group, seg_ids in (topic_index or {}).items():
        if not seg_ids or group not in catalog.groups:
            continue
        produced = any(catalog.slot(f.slot_id).group == group and f.status != "not_discussed" for f in profile.facts.values())
        if produced:
            continue
        if catalog.groups[group].list_entity:
            identity = GROUP_IDENTITY.get(group)
            if identity:
                rows = [eid for eid, kind in entities.items() if identity.startswith(ENTITY_PREFIX.get(kind, "~"))]
                for inst in rows or [None]:
                    want(identity, inst, "topic_index")
            continue
        for slot in catalog.in_group(group):
            if slot.sensitivity_tier == "C" or not slot.exported:
                continue
            for inst in persons if slot.per_person else [None]:
                want(slot.slot_id, inst, "topic_index")
    return list(items.values())


def is_missing(profile: Profile, item: ExpectedFact) -> bool:
    """Still to be asked: no fact yet, or a fact still ``not_discussed``."""
    if item.instance is None and catalog_list_item(item):
        return True
    f = profile.facts.get(item.fact_id)
    return f is None or (f.status == "not_discussed" and f.review.status != "rejected")


def catalog_list_item(item: ExpectedFact) -> bool:
    return "[]" in item.slot_id and item.instance is None


def question_text(item: ExpectedFact) -> str:
    return f"Does this conversation state or imply {item.label}?"


# --------------------------------------------------------------------------- the omission-check agent


class CoverageAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(description="the question's id, e.g. q01")
    answer: Literal["present", "absent"] = Field(description="present when the conversation states or implies it, even as 'I don't know'")
    segment_ids: list[str] = Field(description="the segments that state or imply it; empty when absent")
    reason: str = Field(description="one short line")


class CoverageAnswers(BaseModel):
    """One answer per question."""

    model_config = ConfigDict(extra="forbid")

    answers: list[CoverageAnswer]


INSTRUCTIONS = """\
You check whether a financial-planning conversation between an adviser and a client covers specific facts.
For each closed question answer present or absent:
- present: the segments state or clearly imply a value or a clear answer for that field and person or object, including a
  range, a rough figure, a correction, or the client saying they do not know or have not decided;
- absent: the field is not discussed, or only mentioned in passing with no answer.
Cite the ids of the segments that state or imply it (empty when absent). Use only the segments shown.
Words marked {word?0.42} were transcribed with low confidence. Answer every question id exactly once."""

coverage_agent: Agent[tuple[frozenset[str], frozenset[str]], CoverageAnswers] = Agent(
    None,
    name="omission_check",
    deps_type=tuple,
    output_type=NativeOutput(CoverageAnswers, name="answers", strict=True),
    instructions=INSTRUCTIONS,
    retries={"output": 2},
)


@coverage_agent.output_validator
def _every_question_once(ctx: RunContext[tuple[frozenset[str], frozenset[str]]], out: CoverageAnswers) -> CoverageAnswers:
    if is_offline(ctx):
        return out
    qids, seg_ids = ctx.deps
    got = [a.question_id for a in out.answers]
    missing, unknown = set(qids) - set(got), set(got) - set(qids)
    dups = {g for g in got if got.count(g) > 1}
    bad_segs = sorted({s for a in out.answers for s in a.segment_ids if s not in seg_ids})
    if missing or unknown or dups or bad_segs:
        raise ModelRetry(
            f"Answer each question id {sorted(qids)} exactly once (missing {sorted(missing)}, unknown {sorted(unknown)}, "
            f"repeated {sorted(dups)}) and cite only the segment ids shown (not {bad_segs})."
        )
    return out


class Retriever:
    """Top segments per question: embedding similarity, boosted by the topic index and entity names (§6.5, §13.3)."""

    def __init__(
        self,
        profile: Profile,
        transcript: Transcript,
        catalog: Catalog,
        *,
        embedder: Embedder,
        topic_index: dict[str, list[str]] | None = None,
    ) -> None:
        self.profile, self.transcript, self.catalog = profile, transcript, catalog
        self.segments = list(transcript.segments)
        self.embedder = embedder
        self.topic_index = topic_index or {}
        texts = [s.text + (" " + s.rewritten_text if s.rewritten_text else "") for s in self.segments]
        self.vectors = embedder.embed_documents(texts) if texts else np.zeros((0, embedder.dim), dtype=np.float32)
        self.lower = [t.lower() for t in texts]

    def top(self, item: ExpectedFact, k: int = TOP_SEGMENTS) -> list[str]:
        if not self.segments:
            return []
        slot = self.catalog.slot(item.slot_id)
        query = f"{item.label}. {slot.question}"
        sims = cosine(self.embedder.embed_queries([query]), self.vectors)[0].astype(float)
        indexed = set(self.topic_index.get(item.group, []))
        names: list[str] = []
        ent = self.profile.entities.get(item.instance or "")
        if ent is not None:
            names = [n.lower() for n in [ent.canonical_name, *ent.aliases] if n and len(n) > 2]
        for i, seg in enumerate(self.segments):
            if seg.segment_id in indexed:
                sims[i] += 0.3
            if names and any(n in self.lower[i] for n in names):
                sims[i] += 0.3
            if seg.speaker_role == "advisor":
                sims[i] -= 0.05  # answers come from the client; questions alone rarely settle a field
        order = np.argsort(-sims, kind="stable")[:k]
        return [self.segments[i].segment_id for i in order]


class Question(BaseModel):
    question_id: str
    item: ExpectedFact
    candidates: list[str]


def batch_prompt(questions: Sequence[Question], transcript: Transcript) -> tuple[str, frozenset[str]]:
    index = transcript.index()
    shown = [sid for sid in dict.fromkeys(s for q in questions for s in q.candidates) if sid in index]
    shown.sort(key=lambda sid: index[sid].t_start)
    seg_lines = "\n".join(format_segment(index[sid]) for sid in shown)
    q_lines = "\n".join(
        f"- {q.question_id}: {question_text(q.item)} Look first at: {', '.join(q.candidates) or 'any segment'}" for q in questions
    )
    prompt = f"Transcript segments:\n{seg_lines}\n\nQuestions (answer present or absent, with the segment ids):\n{q_lines}"
    return prompt, frozenset(shown)


async def ask(
    questions: Sequence[Question],
    *,
    transcript: Transcript,
    pins: VersionPins | None,
    batch_size: int = BATCH,
    usage: Usage | None = None,
    resolved: Resolved | None = None,
    errors: list[str] | None = None,
    offline: list[bool] | None = None,
) -> dict[str, CoverageAnswer]:
    """Closed questions, ``batch_size`` per verifier call.

    A question the verifier did not answer is treated as absent, but never silently: a batch the provider
    failed on is reported in ``errors`` (one line per batch, naming its question ids), and a batch the offline
    stand-in answered (a stub verifier role) appends ``True`` to ``offline``, so the caller can tell "nothing
    missing" from "the check did not run".
    """
    resolved = resolved or resolve("verifier", pins, empty_json='{"answers": []}')
    out: dict[str, CoverageAnswer] = {}
    for start in range(0, len(questions), batch_size):
        batch = list(questions[start : start + batch_size])
        prompt, shown = batch_prompt(batch, transcript)
        outcome = await run_guarded(coverage_agent, prompt, resolved, deps=(frozenset(q.question_id for q in batch), shown))
        if usage is not None:
            usage.requests += outcome.usage.requests
            usage.input_tokens += outcome.usage.input_tokens
            usage.output_tokens += outcome.usage.output_tokens
            usage.failures += outcome.usage.failures
        if outcome.output is None and errors is not None:
            ids = ", ".join(q.question_id for q in batch)
            errors.append(f"omission check batch not answered ({ids}): {outcome.error or 'no output'}")
        if offline is not None:  # the stand-in names itself on its responses, whatever model the pin or an override chose
            offline.append(getattr(outcome.response, "model_name", None) == OFFLINE)
        for a in outcome.output.answers if outcome.output else []:
            out[a.question_id] = a
    return out


# --------------------------------------------------------------------------- reopen and the disagree-twice rule


class CoverageResult(BaseModel):
    profile: Profile
    ops: list[StateOp] = []  # applied
    rejected: list[RejectedOp] = []
    expected: list[ExpectedFact] = []
    asked: list[Question] = []
    answers: dict[str, CoverageAnswer] = {}  # question id → answer (last round)
    reopened: list[str] = []  # fact ids the reopen filled
    contradicted: list[str] = []  # fact ids marked contradicted after two disagreements
    unresolved: list[str] = []  # items that disagreed twice but cannot hold a contradicted fact (no prior to show)
    rounds: int = 0
    usage: dict[str, int] = {}
    skipped: str | None = None  # why the check did not run at all (a stub verifier)
    errors: list[str] = []  # verifier batches that failed, and reopen calls that raised: "absent" by default, never silently
    unanswered: list[str] = []  # fact ids whose question got no answer (their batch failed)


def _renumber(profile: Profile, ops: list[StateOp]) -> list[StateOp]:
    seq = profile.next_seq()
    out = []
    for i, op in enumerate(ops):
        payload = dict(op.payload)
        if op.op == "ADD":
            payload["reopened_by_coverage"] = True
        out.append(op.model_copy(update={"seq": seq + i, "payload": payload}))
    return out


def _resolved(profile: Profile, item: ExpectedFact, applied: list[StateOp], catalog: Catalog) -> bool:
    if catalog_list_item(item):
        return any(
            op.fact_id in profile.facts
            and catalog.slot(profile.facts[op.fact_id].slot_id).group == item.group
            and profile.facts[op.fact_id].status != "not_discussed"
            for op in applied
        )
    f = profile.facts.get(item.fact_id)
    return f is not None and f.status != "not_discussed"


def _context_evidence(transcript: Transcript, seg_ids: list[str]) -> list[Evidence]:
    index = transcript.index()
    out = []
    for sid in seg_ids:
        seg = index.get(sid)
        if seg is None:
            continue
        out.append(
            Evidence(
                segment_id=sid,
                char_start=0,
                char_end=len(seg.text),
                quote=seg.text,
                speaker_role=seg.speaker_role,
                t_start=seg.t_start,
                t_end=seg.t_end,
                asr_mean_conf=seg.mean_conf,
                role="context",
            )
        )
    return out


def contradicted_spec(profile: Profile, item: ExpectedFact, catalog: Catalog, transcript: Transcript, seg_ids: list[str]) -> tuple | None:
    """The op that marks a field ``contradicted`` after two disagreements, or None when it cannot hold one."""
    slot = catalog.slot(item.slot_id)
    note = "the omission check found this discussed twice, but extraction found no value"
    f = profile.facts.get(item.fact_id)
    prior: Quantity | None = None
    if slot.population_prior is not None:
        from ..binding import quantity_unit

        unit, period = quantity_unit(slot)
        prior = Quantity(dist=slot.population_prior, unit=unit, period=period)  # type: ignore[arg-type]
    seg = seg_ids[0] if seg_ids else None
    if f is not None:
        payload: dict[str, Any] = {"status": "contradicted", "reason": note}
        if not f.hypotheses:
            if prior is None:
                return None
            payload["add_hypotheses"] = [Hypothesis(value=prior, weight=1.0, rationale="population prior, shown for review: " + note)]
        return ("UPDATE", item.fact_id, seg, payload, "verifier")
    if catalog_list_item(item) or prior is None:
        return None
    person = item.instance if slot.per_person else None
    row = None
    if slot.per_entity and item.instance:
        ent = profile.entities.get(item.instance)
        row = ent.fna_id if ent is not None and ent.fna_id else item.instance
    refs = [{"client": "person.client_primary", "spouse": "person.spouse"}.get(person or "", "")] if person else [item.instance]
    payload = {
        "slot_id": slot.slot_id,
        "instance_key": item.instance,
        "fna_path": slot.fna_path_for(person, row),
        "source": "call",
        "status": "contradicted",
        "hypotheses": [Hypothesis(value=prior, weight=1.0, rationale="population prior, shown for review: " + note)],
        "evidence": _context_evidence(transcript, seg_ids),
        "entity_refs": [r for r in refs if r and r in profile.entities],
        "reopened_by_coverage": True,
        "provenance": Provenance(run_id=profile.run_id, schema_version=profile.schema_version),
    }
    return ("ADD", item.fact_id, seg, payload, "verifier")


async def check_coverage(
    profile: Profile,
    *,
    transcript: Transcript,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    reopen: Reopen,
    topic_index: dict[str, list[str]] | None = None,
    embedder: Embedder | None = None,
    batch_size: int = BATCH,
    max_disagreements: int = MAX_DISAGREEMENTS,
    on_profile: OnProfile | None = None,
) -> CoverageResult:
    """The omission check over the whole profile (§9.1d); every change goes through the reducer.

    ``on_profile`` is called with the profile after every applied batch, so a ``reopen`` callable bound to a
    live view of the run (``extract.probes.make_reopen``) extracts against the profile as it now stands.
    """
    catalog = catalog or load_catalog(profile.schema_version)
    embedder = embedder or get_embedder((pins.embedding_model if pins else None) or "hashed-256")
    expected = expected_facts(profile, catalog, topic_index=topic_index)
    retriever = Retriever(profile, transcript, catalog, embedder=embedder, topic_index=topic_index)
    pending = [it for it in expected if is_missing(profile, it)]
    result = CoverageResult(profile=profile, expected=expected)
    resolved = resolve("verifier", pins, empty_json='{"answers": []}')
    usage = Usage()
    disagreements: dict[str, int] = {}
    cited: dict[str, list[str]] = {}
    n = 0

    def changed(p: Profile) -> Profile:
        if on_profile is not None:
            on_profile(p)
        return p

    while pending and result.rounds < max_disagreements:
        result.rounds += 1
        questions = []
        for it in pending:
            n += 1
            questions.append(Question(question_id=f"q{n:02d}", item=it, candidates=retriever.top(it)))
        result.asked += questions
        offline: list[bool] = []
        answers = await ask(
            questions,
            transcript=transcript,
            pins=pins,
            batch_size=batch_size,
            usage=usage,
            resolved=resolved,
            errors=result.errors,
            offline=offline,
        )
        result.answers.update(answers)
        if offline and all(offline):
            result.skipped = OFFLINE_SKIP
        else:
            result.unanswered += [
                q.item.fact_id for q in questions if q.question_id not in answers and q.item.fact_id not in result.unanswered
            ]
        next_round: list[ExpectedFact] = []
        for q in questions:
            a = answers.get(q.question_id)
            if a is None or a.answer != "present":
                continue
            segs = [s for s in a.segment_ids if s] or q.candidates
            cited[q.item.fact_id] = segs
            try:
                ops = _renumber(profile, list(await reopen(q.item.slot_id, q.item.instance, segs)))
            except Exception as e:  # noqa: BLE001 - one field's extraction failing must not end the check; it is reported
                log.warning("reopening %s failed: %s", q.item.fact_id, e)
                result.errors.append(f"reopening {q.item.fact_id} failed: {type(e).__name__}: {e}")
                continue
            applied: list[StateOp] = []
            if ops:
                r = apply_state_ops(profile, ops)
                profile, applied = changed(r.profile), r.applied
                result.ops += r.applied
                result.rejected += r.rejected
            if _resolved(profile, q.item, applied, catalog):
                result.reopened += [op.fact_id for op in applied if op.fact_id not in result.reopened] or [q.item.fact_id]
                continue
            disagreements[q.item.fact_id] = disagreements.get(q.item.fact_id, 0) + 1
            if disagreements[q.item.fact_id] < max_disagreements:
                next_round.append(q.item)
                continue
            spec = contradicted_spec(profile, q.item, catalog, transcript, segs)
            if spec is None:
                result.unresolved.append(q.item.fact_id)
                continue
            r = apply_state_ops(profile, make_ops(profile, [spec]))
            profile = changed(r.profile)
            result.ops += r.applied
            result.rejected += r.rejected
            if r.applied:
                result.contradicted.append(q.item.fact_id)
        pending = next_round
    if result.errors:
        log.warning("omission check: %d problem(s) on call %s: %s", len(result.errors), profile.call_id, "; ".join(result.errors[:3]))
    # Record what the check found on the facts it touched.
    for fid in set(result.reopened) | set(result.contradicted):
        if fid in profile.facts:
            f = profile.facts[fid]
            profile = annotate(
                profile,
                fid,
                verification={
                    **f.verification,
                    "coverage": {
                        "reopened": fid in result.reopened,
                        "contradicted": fid in result.contradicted,
                        "disagreements": disagreements.get(fid, 0),
                        "segments": cited.get(fid, []),
                    },
                },
            )
    result.profile = changed(profile) if (result.reopened or result.contradicted) else profile
    result.usage = usage.as_dict()
    return result
