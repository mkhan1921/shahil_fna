"""Follow-up generation — ``draft_followups(items)`` (spec §10.5).

One call to an agent on the frontier model with every selected item, its evidence quotes and its schema
question. The agent merges items one question can settle (a daughter's study country, start year and
cost become one question about her study plans) and asks for a range or a choice, not a point ("Would
you say 60, 63 or 65 is most realistic?"), so the answer maps cleanly to an intermediate form. Its output
is a strict-friendly list (item ids, question, answer form) that code turns into ``FollowUp``s: the
advisor's ``why`` line and ``expected_pos_change`` come from the VOI numbers, never from the model.

A validator rejects (and the agent retries) any client-facing text that mentions probabilities, odds,
confidence, scores, the transcript or the model, any unknown item id, and any numeric or categorical
question that offers neither a range nor a choice. Follow-ups are ordered by ``expected_pos_change`` and
capped at 8. If the agent cannot produce valid drafts (stub pins, provider failure) template questions
built from the schema are used instead, merged per person or object and topic; a selected item the agent
left without a question gets a template question too, before the ordering and the cap. Amounts quoted in
template questions are rounded to two significant figures.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

from .. import stats
from ..binding import describe_value
from ..models.dists import Quantity
from ..models.extraction import FORM_TYPES
from ..models.facts import Fact, Provenance
from ..models.profile import FollowUp, Profile
from ..models.runs import VersionPins
from ..schema.slots import Catalog, SlotDef, load_catalog
from ..verify.agents import is_offline, resolve, run_guarded
from ..verify.claims import noun, subject
from .clarify import ClarifyDecision
from .priority import PriorityItem

MAX_QUESTIONS = 8
MAX_ITEMS = 24  # items offered to one drafting call
NUMERIC_FORMS = frozenset(
    {
        "AMOUNT_EXACT",
        "AMOUNT_APPROX",
        "AMOUNT_RANGE",
        "AMOUNT_BOUND",
        "AMOUNT_VAGUE",
        "AMOUNT_RELATIVE",
        "AGE",
        "RATE",
        "DATE_ABS",
        "DURATION_FROM_ANCHOR",
    }
)
FormType = Literal[
    "AMOUNT_EXACT", "AMOUNT_APPROX", "AMOUNT_RANGE", "AMOUNT_BOUND", "AMOUNT_RELATIVE", "AMOUNT_VAGUE", "DATE_ABS", "DURATION_FROM_ANCHOR",
    "AGE", "RATE", "PROBABILITY_EVENT", "CATEGORY", "BOOLEAN", "TEXT",
]  # fmt: skip
assert set(FormType.__args__) == set(FORM_TYPES)  # type: ignore[attr-defined]

# Client-facing text never mentions probabilities, odds, confidence, scores or how the system works (§10.5).
BANNED = re.compile(
    r"(\bprobabilit\w*|\blikelihood\b|\bodds\b|\bper ?cent(?:age)? chance\b|%\s*chance|\bchance of\b"
    r"|\bconfiden\w*|\bcertainty\b|\bscor(?:e|es|ing)\b"
    r"|\bmodel\b|\bAI\b|\bartificial intelligence\b|\balgorithm\w*|\bLLM\b|\bthe system\b|\bour system\b|\btranscri\w*|\bp_correct\b"
    r"|\bextract\w*|\bsensitivity\b|\bcalibrat\w*)",
    re.IGNORECASE,
)
RANGE_OR_CHOICE = re.compile(r"(\bbetween\b|\bor\b|\brange\b|\broughly\b|\bclosest\b|\bcloser\b|\bwhich of\b|–)", re.IGNORECASE)


def banned_terms(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in BANNED.finditer(text)})


# --------------------------------------------------------------------------- the agent


class DraftFollowUp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[str] = Field(description="the ids of the items this one question settles, e.g. ['i1', 'i3']")
    question: str = Field(description="the question, in plain language, addressed to the client; offers a range or a choice")
    answer_type: FormType = Field(description="the form the answer will take, one of the items' answer forms")


class Drafts(BaseModel):
    """The follow-up questions, most important first."""

    model_config = ConfigDict(extra="forbid")

    followups: list[DraftFollowUp]


INSTRUCTIONS = """\
You draft follow-up questions an adviser will put to a client after a financial-planning conversation.
Each item is a field the plan needs settled, with what the client said and the field's question.
Rules:
- Merge items that one question can settle (for example a child's study plans: where, when and the cost), and list every
  item id the question settles.
- Ask for a range or a choice, not a single number: "Would you say 60, 63 or 65 is most realistic?",
  "Roughly between which amounts a month?". For yes/no fields ask a plain yes or no question.
- Plain, warm South African English, addressed to the client as "you", one or two short sentences, no jargon.
- Never mention probabilities, odds, chances, confidence, scores, the recording or transcript, or any system or model.
- Keep the most important items first; draft at most {max_questions} questions."""


@dataclass
class DraftDeps:
    ids: frozenset[str]
    forms: dict[str, frozenset[str]]  # item id → the answer forms its slot allows


followup_agent: Agent[DraftDeps, Drafts] = Agent(
    None,
    name="followup_drafter",
    deps_type=DraftDeps,
    output_type=NativeOutput(Drafts, name="followups", strict=True),
    instructions=INSTRUCTIONS.format(max_questions=MAX_QUESTIONS),
    retries={"output": 2},
)


@followup_agent.output_validator
def _client_safe(ctx: RunContext[DraftDeps], out: Drafts) -> Drafts:
    if is_offline(ctx):
        return out
    problems = []
    for n, d in enumerate(out.followups, start=1):
        unknown = [i for i in d.item_ids if i not in ctx.deps.ids]
        if not d.item_ids or unknown:
            problems.append(f"question {n} must list the item ids it settles (unknown: {unknown})")
        if not d.question.strip():
            problems.append(f"question {n} is empty")
        bad = banned_terms(d.question)
        if bad:
            problems.append(f"question {n} mentions {', '.join(bad)}; client-facing text never does")
        allowed = set().union(*(ctx.deps.forms.get(i, frozenset()) for i in d.item_ids if i in ctx.deps.ids)) if d.item_ids else set()
        if allowed and d.answer_type not in allowed:
            problems.append(f"question {n} answer_type {d.answer_type} is not one of {sorted(allowed)}")
        if d.answer_type in NUMERIC_FORMS | {"CATEGORY"} and not RANGE_OR_CHOICE.search(d.question):
            problems.append(f"question {n} must offer a range or a choice")
    if problems:
        raise ModelRetry("; ".join(problems))
    return out


# --------------------------------------------------------------------------- items


class FollowupItem(BaseModel):
    item_id: str
    fact_id: str
    slot_id: str
    instance: str | None
    group: str
    label: str  # client-facing: "your target retirement age"
    question: str  # the schema question
    current: str  # what the profile holds now, as a short phrase
    status: str
    quotes: list[str] = []
    forms: list[str] = []
    expected_pos_change: float = 0.0
    why: str = ""


def client_label(fact: Fact, profile: Profile, catalog: Catalog) -> str:
    """'your target retirement age', "your spouse's gross salary", "Thandi's tertiary study cost"."""
    slot = catalog.slot(fact.slot_id)
    s, sp = subject(fact, profile, catalog)
    n = noun(slot)
    if slot.per_person and fact.instance_key in ("client", "spouse", None):
        return f"your spouse's {n}" if fact.instance_key == "spouse" else f"your {n}"
    if fact.slot_id == "expenses.items[]":
        return s.replace("the household's", "your household's")
    name = profile.entities[fact.instance_key].canonical_name if fact.instance_key in profile.entities else ""
    if name and slot.per_entity:
        return f"the {n} for {name}"
    return f"your {n}" if sp.startswith("the household") else f"the {n} of {s}"


def build_items(profile: Profile, selected: list[tuple[ClarifyDecision, PriorityItem]], *, catalog: Catalog) -> list[FollowupItem]:
    items = []
    for n, (dec, prio) in enumerate(selected, start=1):
        slot = catalog.slot(prio.slot_id)
        inst = prio.fact_id.split("#", 1)[1] if "#" in prio.fact_id else None
        fact = profile.facts.get(prio.fact_id) or Fact(
            fact_id=prio.fact_id,
            slot_id=slot.slot_id,
            instance_key=inst,
            fna_path="",
            status="not_discussed",
            provenance=Provenance(run_id=profile.run_id),
        )
        items.append(
            FollowupItem(
                item_id=f"i{n}",
                fact_id=prio.fact_id,
                slot_id=slot.slot_id,
                instance=inst,
                group=slot.group,
                label=client_label(fact, profile, catalog),
                question=slot.question,
                current=describe_value(fact.value) if fact.value is not None else "not discussed",
                status=fact.status,
                quotes=[e.quote for e in fact.evidence if e.role in ("supports", "revises", "contradicts") and e.quote][:3],
                forms=list(slot.allowed_forms),
                expected_pos_change=dec.expected_pos_change,
                why=prio.why,
            )
        )
    return items


def draft_prompt(items: list[FollowupItem]) -> str:
    lines = []
    for it in items:
        said = "; ".join(f'"{q}"' for q in it.quotes) or "nothing specific"
        lines.append(
            f"- {it.item_id}: {it.label}. Field question: {it.question} Currently: {it.current} ({it.status.replace('_', ' ')}). "
            f"Client's words: {said}. Answer forms: {', '.join(it.forms)}."
        )
    return "Items to settle, most important first:\n" + "\n".join(lines)


# --------------------------------------------------------------------------- templates (fallback)


def _round_amount(x: float) -> float:
    """Two significant figures for a client-facing amount: R53,865 reads as R54,000 (a range, not a quote back)."""
    if abs(x) < 100:
        return round(x)
    digits = len(str(int(abs(x)))) - 2
    return round(x, -digits)


def _fmt(x: float, q: Quantity | None, slot: SlotDef) -> str:
    unit = (
        q.unit if q is not None else {"rand": "ZAR", "rand_monthly": "ZAR", "rand_annual": "ZAR", "decimal": "pct"}.get(slot.fna_unit, "")
    )
    if unit == "ZAR":
        return f"R{_round_amount(x):,.0f}"
    if unit == "pct":
        return f"{x * 100:.0f}%"
    if slot.value_type in ("date", "year"):
        return stats.year_to_iso(x)[:4]
    if slot.value_type in ("age", "years", "months", "count"):
        return f"{round(x):d}"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.3g}"


def template_question(it: FollowupItem, fact: Fact | None, slot: SlotDef) -> tuple[str, str]:
    """(question, answer form) built from the schema: a range or a choice, never a point.

    A field with no value is asked open (a range is still what we ask for): quoting its slot's population
    prior back to the client would only anchor the answer.
    """
    q = fact.value if fact is not None else None
    dist = q.dist if q is not None else None
    prior = slot.population_prior  # orders the choices of a categorical field with no value
    label = it.label
    if slot.value_type in ("boolean", "probability"):
        return f"Could you confirm {label}: is it yes or no?", "BOOLEAN" if "BOOLEAN" in it.forms else it.forms[0]
    if slot.categories:
        cats = [c for c in slot.categories if c]
        if (dist or prior) is not None:
            try:
                probs = stats.categorical_probs(dist or prior)
                cats = sorted(cats, key=lambda c: -probs.get(c, 0.0))
            except TypeError:
                pass
        options = cats[:3]
        return f"Which of these best describes {label}: {', '.join(options[:-1])} or {options[-1]}?", "CATEGORY"
    if slot.value_type == "text":
        return f"Could you tell us a little more about {label}?", "TEXT"
    form = next(
        (f for f in ("AMOUNT_RANGE", "AGE", "RATE", "DURATION_FROM_ANCHOR", "DATE_ABS") if f in it.forms),
        it.forms[0] if it.forms else "TEXT",
    )
    if dist is not None:
        try:
            lo, hi = stats.quantile(dist, 0.1), stats.quantile(dist, 0.9)
            if hi > lo:
                low, high = _fmt(lo, q, slot), _fmt(hi, q, slot)
                return f"Roughly where would you put {label}: closer to {low} or to {high}, or somewhere in between?", form
        except (TypeError, ValueError):
            pass
    return f"Roughly what range would you put on {label}?", form


def template_drafts(items: list[FollowupItem], profile: Profile, catalog: Catalog) -> list[DraftFollowUp]:
    """One question per item, merged per person or object and topic (the deterministic fallback)."""
    groups: dict[tuple[str, str | None], list[FollowupItem]] = {}
    for it in items:
        groups.setdefault((it.group, it.instance), []).append(it)
    out = []
    for members in groups.values():
        if len(members) == 1:
            it = members[0]
            text, form = template_question(it, profile.facts.get(it.fact_id), catalog.slot(it.slot_id))
            out.append(DraftFollowUp(item_ids=[it.item_id], question=text, answer_type=form))  # type: ignore[arg-type]
            continue
        labels = [m.label for m in members]
        joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
        first = members[0]
        _, form = template_question(first, profile.facts.get(first.fact_id), catalog.slot(first.slot_id))
        out.append(
            DraftFollowUp(
                item_ids=[m.item_id for m in members],
                question=f"Could you give us a rough range or your best options for {joined}?",
                answer_type=form,
            )
        )  # type: ignore[arg-type]
    return out


# --------------------------------------------------------------------------- drafting


def _followup_id(call_id: str, fact_ids: list[str]) -> str:
    return "fu_" + hashlib.sha1(f"{call_id}|{'|'.join(sorted(fact_ids))}".encode()).hexdigest()[:10]


class DraftResult(BaseModel):
    followups: list[FollowUp] = []
    drafted_by: Literal["model", "template", "none"] = "none"
    usage: dict[str, int] = {}
    error: str | None = None


async def draft_followups(
    profile: Profile,
    decisions: dict[str, ClarifyDecision],
    priorities: list[PriorityItem],
    *,
    catalog: Catalog | None = None,
    pins: VersionPins | None = None,
    max_questions: int = MAX_QUESTIONS,
) -> DraftResult:
    """Follow-up questions for the facts §10.4 sends to the client, ordered by expected change, top 8."""
    catalog = catalog or load_catalog(profile.schema_version)
    by_id = {p.fact_id: p for p in priorities}
    selected = sorted(
        ((d, by_id[fid]) for fid, d in decisions.items() if d.action == "followup" and fid in by_id),
        key=lambda t: (-t[0].expected_pos_change, -t[1].priority, t[0].fact_id),
    )[:MAX_ITEMS]
    if not selected:
        return DraftResult()
    items = build_items(profile, selected, catalog=catalog)
    item_by_id = {it.item_id: it for it in items}
    deps = DraftDeps(ids=frozenset(item_by_id), forms={it.item_id: frozenset(it.forms) for it in items})
    resolved = resolve("frontier", pins, empty_json='{"followups": []}')
    outcome = await run_guarded(followup_agent, draft_prompt(items), resolved, deps=deps)
    usage = outcome.usage
    drafts = [d for d in (outcome.output.followups if outcome.output else []) if d.item_ids and not banned_terms(d.question)]
    drafted_by: Literal["model", "template", "none"] = "model"
    if not drafts:
        drafts, drafted_by = template_drafts(items, profile, catalog), "template"
    else:
        # Every selected item was chosen by §10.4; one the model left without a question still gets asked, from
        # the schema template, and the ordering and cap below decide whether it makes the cut.
        covered = {i for d in drafts for i in d.item_ids}
        left = [it for it in items if it.item_id not in covered]
        if left:
            drafts = drafts + template_drafts(left, profile, catalog)
    out: list[FollowUp] = []
    seen: set[str] = set()
    for d in drafts:
        members = [item_by_id[i] for i in dict.fromkeys(d.item_ids) if i in item_by_id and item_by_id[i].fact_id not in seen]
        if not members:
            continue
        seen.update(m.fact_id for m in members)
        lead = max(members, key=lambda m: m.expected_pos_change)
        fact_ids = [m.fact_id for m in members]
        out.append(
            FollowUp(
                followup_id=_followup_id(profile.call_id, fact_ids),
                fact_ids=fact_ids,
                question=d.question.strip(),
                why=lead.why,
                expected_pos_change=max(m.expected_pos_change for m in members),
                answer_type=d.answer_type,
            )
        )
    out.sort(key=lambda f: -f.expected_pos_change)
    return DraftResult(followups=out[:max_questions], drafted_by=drafted_by if out else "none", usage=usage.as_dict(), error=outcome.error)
