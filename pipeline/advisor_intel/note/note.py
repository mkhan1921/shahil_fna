"""The adviser's meeting note, rendered from reviewed facts, never from the transcript (spec §11.6).

- A Pydantic AI agent returns ``list[NoteSentence]``: each sentence names the section it belongs to
  and the ``fact_id``s it uses (a strict-friendly schema: every field required, no unions).
- Code then removes any sentence that cites no fact, cites an unknown or rejected fact, or whose
  numbers or named entities differ from its cited facts.
- Editing a fact regenerates only the sentences that cite it (``update_note``); editing the note
  never changes facts.
- The note renders the FNA's narrative fields: ``engagement.reasonForAdvice``, ``clientObjectives``,
  ``clientConcerns`` and a draft ``advice.summary``. Objectives and concerns in the client's own
  words come from the engagement text slots, which are facts with aligned spans like any other.

With a ``"test"`` model pin (offline runs) the note is written by a deterministic template over the
same facts, so the pipeline still produces a valid, fully grounded note.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from .. import llm, stats
from ..binding import describe_value
from ..models.dists import Point
from ..models.facts import Fact
from ..models.fna import FnaDocument
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..schema.slots import Catalog, load_catalog
from ..storage.store import content_hash

NoteSection = Literal["reason_for_advice", "objectives", "concerns", "summary"]
SECTIONS: tuple[NoteSection, ...] = ("reason_for_advice", "objectives", "concerns", "summary")
NARRATIVE_FIELDS: dict[NoteSection, str] = {
    "reason_for_advice": "engagement.reasonForAdvice",
    "objectives": "engagement.clientObjectives",
    "concerns": "engagement.clientConcerns",
    "summary": "advice.summary",
}
SECTION_SLOTS: dict[NoteSection, str] = {
    "reason_for_advice": "engagement.reasonForAdvice",
    "objectives": "engagement.clientObjectives",
    "concerns": "engagement.clientConcerns",
}
SECTION_TITLES: dict[NoteSection, str] = {
    "reason_for_advice": "Reason for advice",
    "objectives": "Client objectives",
    "concerns": "Client concerns",
    "summary": "Summary (draft)",
}
PROMPT_VERSION = "meeting_note-v1"

RemovalReason = Literal["no_facts", "unknown_fact", "rejected_fact", "number_mismatch", "entity_mismatch"]


class NoteSentence(BaseModel):
    """One sentence of the meeting note and the facts it uses."""

    section: NoteSection = Field(description="reason_for_advice | objectives | concerns | summary")
    text: str = Field(description="one sentence, third person, numbers copied exactly from the cited facts")
    fact_ids: list[str] = Field(description="every fact the sentence uses; at least one")


class RemovedSentence(BaseModel):
    sentence: NoteSentence
    reason: RemovalReason
    detail: str = ""


class MeetingNote(BaseModel):
    call_id: str
    run_id: str
    profile_version: int
    sentences: list[NoteSentence]
    removed: list[RemovedSentence] = []
    fact_hashes: dict[str, str] = {}  # cited fact → its content hash when the sentence was written
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def section_text(self, section: NoteSection) -> str:
        return " ".join(s.text.strip() for s in self.sentences if s.section == section)

    def narrative_fields(self) -> dict[str, str]:
        """FnaDocument path → rendered text, for the sections the note has."""
        return {path: t for sec, path in NARRATIVE_FIELDS.items() if (t := self.section_text(sec))}

    def text(self) -> str:
        parts = []
        for sec in SECTIONS:
            body = self.section_text(sec)
            if body:
                parts.append(f"{SECTION_TITLES[sec]}\n{body}")
        return "\n\n".join(parts)


INSTRUCTIONS = """\
You write a South African financial adviser's meeting note from a table of reviewed facts about the client.
Return a list of sentences. Each sentence belongs to one section and lists the fact_ids it uses.

Sections:
- reason_for_advice: why the client came for advice now.
- objectives: what the client wants to achieve. Prefer the client's own words when a text fact quotes them.
- concerns: what worries the client. Prefer the client's own words when a text fact quotes them.
- summary: a factual draft of the advice summary: the situation and what was discussed. No recommendations.

Rules:
- Use only the facts in the table. Every sentence cites at least one fact_id; cite every fact a sentence uses.
- Copy numbers, ages, dates and amounts exactly as the table gives them. Do not add, convert, total or round them.
- Name a person, policy, fund or other item only when a cited fact is about it.
- Facts marked unknown are things the client did not know; say so plainly if you mention them.
- Write plain, professional third-person English ("The client ...", "Her spouse ..."). One idea per sentence.
"""


# --------------------------------------------------------------------------- the fact table


def _person(instance: str | None) -> str | None:
    return {"client": "person.client_primary", "spouse": "person.spouse"}.get(instance or "")


def fact_entities(f: Fact) -> set[str]:
    out = set(f.entity_refs)
    if f.instance_key:
        out.add(_person(f.instance_key) or f.instance_key)
    return out


def note_facts(profile: Profile) -> list[Fact]:
    """The facts a note may use: what the call established or changed, and nothing the reviewer rejected."""
    out = []
    for f in sorted(profile.facts.values(), key=lambda x: x.fact_id):
        if f.review.status == "rejected" or f.change == "removed":
            continue
        if f.status == "not_discussed":
            continue
        if f.value is None and f.status != "explicitly_unknown":
            continue
        out.append(f)
    return out


def _label(f: Fact, catalog: Catalog) -> str:
    try:
        slot = catalog.slot(f.slot_id)
        label = slot.label or slot.fna_path
    except KeyError:
        label = f.slot_id
    who = {"client": "client", "spouse": "spouse"}.get(f.instance_key or "")
    return f"{label} ({who})" if who else label


def _value_text(f: Fact) -> str:
    if f.status == "explicitly_unknown":
        return "unknown (the client does not know or has not decided)"
    q = f.value
    if q is not None and isinstance(q.dist, Point) and isinstance(q.dist.value, str):
        return q.dist.value
    return describe_value(q)


def fact_table(facts: Iterable[Fact], profile: Profile, catalog: Catalog) -> str:
    rows = []
    for f in facts:
        names = sorted({profile.entities[e].canonical_name for e in fact_entities(f) if e in profile.entities})
        quote = next((e.quote for e in f.evidence if e.role in ("supports", "revises")), "")
        parts = [
            f"fact_id={f.fact_id}",
            f"field={_label(f, catalog)}",
            f"value={_value_text(f)}",
            f"status={f.status}",
            f"change={f.change}",
        ]
        if f.prior_value is not None and f.change == "changed":
            parts.append(f"previously={describe_value(f.prior_value)}")
        if names:
            parts.append(f"about={', '.join(names)}")
        if quote:
            parts.append(f'said="{quote}"')
        rows.append(" | ".join(parts))
    return "\n".join(rows)


# --------------------------------------------------------------------------- validation


_SCALE = {"thousand": 1e3, "k": 1e3, "bar": 1e3, "million": 1e6, "mil": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}
_NUM = re.compile(
    r"(?P<cur>R|\$|£|€)?\s?(?P<num>\d{1,3}(?:[ , ]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)\s?(?P<scale>thousand|million|billion|mil|bn|bar|k|m)?\b(?P<pct>\s?%|\s?percent)?",
    re.IGNORECASE,
)
_THOUSANDS = re.compile(r"\d{1,3}(?:[ , ]\d{3})+(?:\.\d+)?")
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_NUM = re.compile(r"\b(" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")(?:[- ](one|two|three|four|five|six|seven|eight|nine))?\b", re.IGNORECASE)


class NumberMention(BaseModel):
    value: float
    resolution: float  # the precision the sentence states the number to
    percent: bool = False
    surface: str = ""
    word: bool = False  # written as a word ("sixty-five")


def numbers_in(text: str) -> list[NumberMention]:
    """Numbers a sentence states: digits with separators, currency, scale words and percentages, and number words."""
    out: list[NumberMention] = []
    for m in _NUM.finditer(text):
        raw = m.group("num")
        # "1 200 000" and "1,200,000" group thousands; "1,2 million" uses a decimal comma.
        digits = re.sub(r"[ , ]", "", raw) if _THOUSANDS.fullmatch(raw) else raw.replace(",", ".")
        v = float(digits)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        res = 10.0 ** (-decimals)
        scale = (m.group("scale") or "").lower()
        if scale == "m" and not m.group("cur"):
            scale = ""  # "65 m" is metres or noise, not millions, without a currency
        if scale:
            mult = _SCALE[scale]
            v *= mult
            res *= mult
        out.append(NumberMention(value=v, resolution=res, percent=bool(m.group("pct")), surface=m.group(0).strip()))
    for m in _WORD_NUM.finditer(text):
        tens, unit = m.group(1).lower(), (m.group(2) or "").lower()
        v = _WORDS[tens] + (_WORDS[unit] if unit and tens in _TENS else 0)
        out.append(NumberMention(value=float(v), resolution=1.0, surface=m.group(0), word=True))
    return out


def _dist_numbers(q: Any) -> list[float]:
    """Numbers a value can be quoted as: its point, quantiles, bounds and support."""
    if q is None:
        return []
    d = q.dist
    vals: list[float] = []
    if isinstance(d, Point):
        v = d.value
        if isinstance(v, bool):
            return []
        if isinstance(v, (int, float)):
            vals.append(float(v))
        elif isinstance(v, str):
            vals.extend(float(x) for x in re.findall(r"\d+(?:\.\d+)?", v))
            if stats._looks_like_date(v):  # noqa: SLF001
                vals.append(float(v[:4]))
        return vals
    if stats.is_categorical(d) or stats.is_boolean(d):
        return []
    try:
        s = stats.summary(d)
        vals.extend(x for x in (s.get("p10"), s.get("p50"), s.get("p90"), s.get("mean")) if x is not None and math.isfinite(x))
    except (TypeError, ValueError):
        pass
    for attr in ("low", "mode", "high", "median", "p10", "p90", "mean"):
        x = getattr(d, attr, None)
        if isinstance(x, (int, float)):
            vals.append(float(x))
    vals.extend(float(x) for x in getattr(d, "support", []) or [])
    for _, comp in getattr(d, "components", []) or []:
        vals.extend(_dist_numbers(type(q)(dist=comp, unit=q.unit, period=q.period)))
    return vals


def allowed_numbers(f: Fact) -> list[float]:
    """Every number a sentence citing ``f`` may state, in the forms a reader would expect."""
    base: list[float] = []
    for q in [f.value, f.prior_value, *(h.value for h in f.hypotheses)]:
        base.extend(_dist_numbers(q))
        if q is not None and q.unit == "date":
            base.extend(float(math.floor(x)) for x in _dist_numbers(q))
    out = list(base)
    unit = f.value.unit if f.value is not None else None
    period = f.value.period if f.value is not None else None
    if unit == "pct":
        out.extend(x * 100 for x in base)
    if unit in ("ZAR", "USD", "GBP", "EUR") and period == "monthly":
        out.extend(x * 12 for x in base)
    if unit in ("ZAR", "USD", "GBP", "EUR") and period == "annual":
        out.extend(x / 12 for x in base)
    for e in f.evidence:  # the client's own words may be quoted
        out.extend(n.value for n in numbers_in(e.quote))
    if f.said:
        out.extend(n.value for n in numbers_in(f.said))
    return out


def _matches(n: NumberMention, allowed: Iterable[float]) -> bool:
    for a in allowed:
        tol = max(n.resolution / 2, 0.005 * abs(a), 1e-9)
        if abs(n.value - a) <= tol:
            return True
    return False


NOTE_SUBJECTS = frozenset({"person.client_primary", "person.spouse", "person.advisor"})
_GENERIC_NAMES = {"client", "spouse", "adviser", "advisor", "child", "parent", "policy", "fund", "goal", "item", "expense", "debt", "home", "house", "car"}


def _entity_names(profile: Profile) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for eid, e in profile.entities.items():
        cands = {e.canonical_name, *e.aliases}
        if e.type == "person":  # people are named by first name too ("Neo"); a shared surname is not a name
            cands |= {c.split()[0] for c in list(cands) if c and " " in c}
        names[eid] = sorted({c for c in cands if c and len(c) >= 3 and c.lower() not in _GENERIC_NAMES and not c.isdigit()}, key=len, reverse=True)
    return names


def check_sentence(s: NoteSentence, profile: Profile, names: dict[str, list[str]] | None = None) -> tuple[RemovalReason, str] | None:
    """Why a sentence must go, or None when every number and name agrees with its cited facts."""
    if not s.fact_ids:
        return "no_facts", ""
    unknown = [fid for fid in s.fact_ids if fid not in profile.facts]
    if unknown:
        return "unknown_fact", ", ".join(unknown)
    facts = [profile.facts[fid] for fid in s.fact_ids]
    rejected = [f.fact_id for f in facts if f.review.status == "rejected" or f.change == "removed"]
    if rejected:
        return "rejected_fact", ", ".join(rejected)
    allowed = [x for f in facts for x in allowed_numbers(f)]
    # The client, spouse and adviser are the note's subjects and may always be named; any other
    # person, policy, fund or item must be what a cited fact is about (the "wrong child" error).
    about = set().union(*(fact_entities(f) for f in facts))
    cited_entities = about | NOTE_SUBJECTS
    counts = {float(len(about - NOTE_SUBJECTS)), float(len(facts))}
    for n in numbers_in(s.text):
        if _matches(n, allowed):
            continue
        if n.word and n.value <= 12:
            continue  # "one of", "two-pot": small number words are prose, not figures
        if n.value <= 12 and n.value in counts:
            continue
        return "number_mismatch", n.surface
    names = names if names is not None else _entity_names(profile)
    lowered = s.text.lower()
    # Names the cited facts themselves carry (the client's own words, the value of a text slot) may be repeated.
    own_words = " ".join(
        [str(f.value.dist.value) for f in facts if f.value is not None and isinstance(f.value.dist, Point) and isinstance(f.value.dist.value, str)]
        + [e.quote for f in facts for e in f.evidence]
        + [f.said for f in facts]
    ).lower()
    for eid, cands in names.items():
        if eid in cited_entities:
            continue
        for c in cands:
            pattern = rf"(?<!\w){re.escape(c.lower())}(?!\w)"
            if re.search(pattern, lowered):
                # A name that also belongs to a cited entity (a shared alias) or that the cited facts quote is fine.
                if any(c in names.get(other, []) for other in cited_entities) or re.search(pattern, own_words):
                    break
                return "entity_mismatch", c
    return None


def validate(sentences: list[NoteSentence], profile: Profile) -> tuple[list[NoteSentence], list[RemovedSentence]]:
    names = _entity_names(profile)
    kept: list[NoteSentence] = []
    removed: list[RemovedSentence] = []
    for s in sentences:
        s = s.model_copy(update={"text": s.text.strip(), "fact_ids": list(dict.fromkeys(s.fact_ids))})
        if not s.text:
            continue
        why = check_sentence(s, profile, names)
        if why is None:
            kept.append(s)
        else:
            removed.append(RemovedSentence(sentence=s, reason=why[0], detail=why[1]))
    return kept, removed


# --------------------------------------------------------------------------- writing


def note_agent(model: Model | str) -> Agent[None, list[NoteSentence]]:
    return Agent(model, output_type=NativeOutput(list[NoteSentence], name="meeting_note", strict=True), instructions=INSTRUCTIONS, name="meeting_note", retries=2)


def _resolve_model(pins: VersionPins, model: Model | str | None) -> tuple[Model | str | None, str, Any]:
    """The model to write with; None means the deterministic template (offline "test" pins)."""
    if model is not None:
        return model, getattr(model, "model_name", str(model)), None
    rm = llm.for_role("frontier", pins)
    if isinstance(rm.model, TestModel):
        return None, "template", None
    return rm.model, rm.name, rm.settings


def template_sentences(facts: list[Fact], profile: Profile, catalog: Catalog) -> list[NoteSentence]:
    """A plain, fully grounded note without a model: one sentence per fact, text slots quoted."""
    out: list[NoteSentence] = []
    for sec, slot in SECTION_SLOTS.items():
        for f in facts:
            if f.slot_id == slot and f.value is not None and isinstance(f.value.dist, Point) and str(f.value.dist.value).strip():
                text = str(f.value.dist.value).strip()
                out.append(NoteSentence(section=sec, text=text if text.endswith(".") else text + ".", fact_ids=[f.fact_id]))
    for f in facts:
        if f.slot_id in SECTION_SLOTS.values():
            continue
        verb = "was updated to" if f.change == "changed" else "is"
        label = _label(f, catalog)
        label = label[:1].lower() + label[1:]
        if f.status == "explicitly_unknown":
            text = f"The {label} is not yet known."
        else:
            text = f"The {label} {verb} {_value_text(f)}."
        out.append(NoteSentence(section="summary", text=text, fact_ids=[f.fact_id]))
    return out


async def _write(facts: list[Fact], profile: Profile, catalog: Catalog, *, pins: VersionPins, model: Model | str | None, task: str) -> tuple[list[NoteSentence], str]:
    m, name, settings = _resolve_model(pins, model)
    if m is None:
        return template_sentences(facts, profile, catalog), name
    prompt = f"{task}\n\nFacts (one per line):\n{fact_table(facts, profile, catalog)}"
    result = await note_agent(m).run(prompt, model_settings=settings)
    return list(result.output), name


def _hashes(profile: Profile, sentences: list[NoteSentence]) -> dict[str, str]:
    ids = {fid for s in sentences for fid in s.fact_ids if fid in profile.facts}
    return {fid: content_hash(profile.facts[fid].model_dump(mode="json", exclude={"review_priority", "voi", "features"})) for fid in sorted(ids)}


async def generate_note(profile: Profile, *, pins: VersionPins, catalog: Catalog | None = None, model: Model | str | None = None) -> MeetingNote:
    """Write the whole note from the profile's reviewed facts and validate every sentence."""
    catalog = catalog or load_catalog(profile.schema_version)
    facts = note_facts(profile)
    if not facts:
        return MeetingNote(call_id=profile.call_id, run_id=profile.run_id, profile_version=profile.version, sentences=[], model="none")
    raw, name = await _write(facts, profile, catalog, pins=pins, model=model, task="Write the meeting note.")
    kept, removed = validate(raw, profile)
    return MeetingNote(
        call_id=profile.call_id, run_id=profile.run_id, profile_version=profile.version, sentences=kept, removed=removed, fact_hashes=_hashes(profile, kept), model=name
    )


def changed_facts(note: MeetingNote, profile: Profile) -> set[str]:
    """Cited facts whose content changed (or that disappeared) since the note was written."""
    now = _hashes(profile, [NoteSentence(section="summary", text="-", fact_ids=list(note.fact_hashes))])
    return {fid for fid, h in note.fact_hashes.items() if now.get(fid) != h}


async def update_note(
    note: MeetingNote, profile: Profile, *, pins: VersionPins, catalog: Catalog | None = None, model: Model | str | None = None, edited: set[str] | None = None
) -> MeetingNote:
    """Regenerate only the sentences that cite an edited fact; every other sentence is kept as written."""
    catalog = catalog or load_catalog(profile.schema_version)
    edited = changed_facts(note, profile) if edited is None else set(edited)
    hit = [i for i, s in enumerate(note.sentences) if edited & set(s.fact_ids)]
    if not hit:
        return note.model_copy(update={"profile_version": profile.version})
    old = [note.sentences[i] for i in hit]
    cited = sorted({fid for s in old for fid in s.fact_ids if fid in profile.facts})
    facts = [profile.facts[fid] for fid in cited if profile.facts[fid].review.status != "rejected"]
    fresh: list[NoteSentence] = []
    if facts:
        listing = "\n".join(f"- [{s.section}] {s.text} (facts: {', '.join(s.fact_ids)})" for s in old)
        task = (
            "Some facts were edited in review. Rewrite only these sentences so they match the facts below; keep their sections, "
            f"and keep sentences short. Sentences to rewrite:\n{listing}"
        )
        fresh, _ = await _write(facts, profile, catalog, pins=pins, model=model, task=task)
    kept, removed = validate(fresh, profile)
    # Splice: each section's regenerated sentences take the place of that section's first rewritten one.
    by_section: dict[str, list[NoteSentence]] = {}
    for s in kept:
        by_section.setdefault(s.section, []).append(s)
    out: list[NoteSentence] = []
    placed: set[str] = set()
    hit_set = set(hit)
    for i, s in enumerate(note.sentences):
        if i not in hit_set:
            out.append(s)
            continue
        if s.section not in placed:
            out.extend(by_section.pop(s.section, []))
            placed.add(s.section)
    for rest in by_section.values():
        out.extend(rest)
    hashes = {**{k: v for k, v in note.fact_hashes.items() if k in {f for s in out for f in s.fact_ids}}, **_hashes(profile, kept)}
    return note.model_copy(
        update={
            "profile_version": profile.version,
            "sentences": out,
            "removed": note.removed + removed,
            "fact_hashes": hashes,
            "generated_at": datetime.now(timezone.utc),
        }
    )


def apply_note(doc: FnaDocument, note: MeetingNote) -> FnaDocument:
    """Write the note's narrative fields into a document (the export carries them, §11.6)."""
    data = doc.model_dump(mode="json")
    for path, text in note.narrative_fields().items():
        head, _, field = path.partition(".")
        data[head][field] = text
    return FnaDocument.model_validate(data)
