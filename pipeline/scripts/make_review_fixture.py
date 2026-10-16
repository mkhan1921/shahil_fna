"""Build the demo review package for the sample household (spec §11, §11.5 file exchange).

The package is what the pipeline hands the adviser after a call: the Mokoena household's existing
FNA (``tests/fixtures/sample_fna.json``) seeded with ``binding.load_prior``, then a short annual-review
call between the adviser (Sipho) and both clients, entered as extractor, deriver and loader ops
through the real reducer. It covers what the review overlay must handle:

- new, changed, confirmed and assumed fields, and two new list rows (a group disability policy and
  some Krugerrands);
- a range ("between sixty and sixty-five, probably sixty-two"), a bimodal value (Kagiso's study
  costs here or in the UK) and a Bernoulli event ("she might study overseas");
- an adviser-suggested tier A value, a contradicted fact (the RA value) and a scale-ambiguous one;
- low, medium and high ``p_correct``, auto-accepted facts, hedges and evidence quotes with segment
  ids and timestamps over a three-speaker transcript;
- blocking flags of five kinds, follow-up questions, an unresolved term and a preview forecast with
  its variance attribution.

Writes the same package to ``tests/fixtures/review_package.json`` (vitest),
``pipeline/tests/fixtures/review_package.json`` (pytest) and ``public/demo/review_package.json``
(the app's "Load demo review"). Run from ``pipeline/``:
``.venv/bin/python scripts/make_review_fixture.py`` (``--check`` only compares).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from advisor_intel import binding  # noqa: E402
from advisor_intel.extract.reducer import annotate, apply_state_ops, check_profile, make_ops  # noqa: E402
from advisor_intel.models import fna as F  # noqa: E402
from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, Point, Quantity, Triangular  # noqa: E402
from advisor_intel.models.entities import Entity  # noqa: E402
from advisor_intel.models.facts import Evidence, Hedge, Hypothesis, Provenance  # noqa: E402
from advisor_intel.models.forecast import ForecastResult  # noqa: E402
from advisor_intel.models.profile import BlockingFlag, FollowUp, Profile  # noqa: E402
from advisor_intel.models.review import ReviewBundle, ReviewPackage  # noqa: E402
from advisor_intel.models.runs import RunState, VersionPins  # noqa: E402
from advisor_intel.models.transcript import NumericSpan, QualityReport, Reading, Segment, Transcript, Word  # noqa: E402
from advisor_intel.schema.slots import fact_id_for, load_catalog  # noqa: E402

# Test copies are indented so a regenerated fixture diffs line by line; the copy the app serves is compact.
OUTPUTS: tuple[tuple[Path, bool], ...] = (
    (REPO / "tests" / "fixtures" / "review_package.json", False),
    (PIPELINE / "tests" / "fixtures" / "review_package.json", False),
    (REPO / "public" / "demo" / "review_package.json", True),
)

ANCHOR = date(2026, 10, 15)
CALL_ID = "call-2026-10-15-mokoena"
RUN_ID = "wf-call-2026-10-15-mokoena"
VERSIONS = VersionPins(
    lexicon_version="1.3",
    calibrator_version="cold-start-2026-10",
    asr_provider="deepgram",
    asr_model_version="nova-3-2026-08",
    frontier_model="frontier-2026-09",
    reasoning_model="reasoning-2026-09",
    verifier_model="verifier-2026-08",
    small_model="small-2026-07",
    embedding_model="embeddinggemma-2-256",
    prompt_versions={"extract_objects": "v3", "extract_fields": "v3", "extract_values": "v4", "orchestrator": "v2"},
)
PROV = Provenance(
    run_id=RUN_ID,
    trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    extractor_model=VERSIONS.reasoning_model,
    extractor_prompt_version="extract_values/v4",
    calibrator_version=VERSIONS.calibrator_version,
    lexicon_version=VERSIONS.lexicon_version,
    schema_version=VERSIONS.schema_version,
)

# --------------------------------------------------------------------------- the call

SPEAKERS = {"advisor": ("spk_0", 0.98), "client_primary": ("spk_1", 0.95), "client_secondary": ("spk_2", 0.91)}

# (role, text, pause before it in seconds). Sipho is the adviser; Thabo the client; Lerato his wife.
DIALOGUE: list[tuple[str, str, float]] = [
    ("advisor", "Morning Thabo, morning Lerato. Thanks for making time for the annual review.", 1.2),
    ("client_primary", "Morning Sipho. Ja, a lot has changed since last year, so the timing is good.", 0.6),
    ("advisor", "Let's start with income. Thabo, last time you were on a hundred thousand a month gross.", 0.8),
    ("client_primary", "That was before the promotion. It's one eighteen now, one hundred and eighteen thousand gross a month, from September.", 0.5),
    ("advisor", "Congratulations. And Lerato, are you still at Brightwater?", 0.9),
    ("client_secondary", "Still there, and still fifty-five a month. No change, unfortunately.", 0.7),
    ("advisor", "And the bonus, Thabo?", 0.6),
    ("client_primary", "The bonus is about a hundred and twenty, give or take, depending on how the year goes.", 0.8),
    ("advisor", "Let's talk about retirement. Last time we planned on sixty-five for you, Thabo.", 1.4),
    ("client_primary", "Honestly, somewhere between sixty and sixty-five. Probably sixty-two, if the numbers allow it.", 1.1),
    ("advisor", "Most people in your position plan on about seventy percent of salary in retirement. Shall we use that?", 0.7),
    ("client_primary", "If you say so. I haven't really thought about it.", 1.6),
    ("advisor", "Now the children. Neo is still at the independent school?", 1.0),
    ("client_secondary", "Yes, and the fees went up. It's about a hundred and five thousand for the year now.", 0.6),
    ("client_primary", "And we're thinking of moving Kagiso to the same school next year.", 0.5),
    ("client_secondary", "She might study overseas one day. My sister's kids all went to the UK.", 0.9),
    ("client_primary", "If she studies here it's about a hundred and fifty a year, but over there it's more like four-fifty.", 0.8),
    ("advisor", "Understood. Let's look at your retirement savings. Thabo, what is the retirement annuity on now?", 1.2),
    ("client_primary", "The RA is sitting at about three-fifty, I think.", 0.4),
    ("client_secondary", "No, it's more like two-eighty. I saw the statement last month.", -0.5),
    ("advisor", "We'll check the statement. And how much is still owing on the bond?", 0.9),
    ("client_primary", "We still owe about eighteen fifty on the bond.", 0.7),
    ("client_primary", "Oh, and I took out disability cover through work in August. I don't know the amount yet.", 1.3),
    ("advisor", "No problem, we'll get the benefit schedule from HR. Any other investments since we last met?", 0.8),
    ("client_primary", "I bought a few Krugerrands. They're in the safe at home, maybe sixty thousand worth.", 1.0),
    ("advisor", "Good to know. Lerato, last time you said you don't have a will.", 1.1),
    ("client_secondary", "We signed new wills in June, both of us, with the attorney.", 0.6),
    ("advisor", "That's a big one done. Did you name a guardian for the children?", 0.8),
    ("client_secondary", "Yes, my sister Palesa.", 0.5),
    ("advisor", "And groceries, still about nine thousand a month?", 1.2),
    ("client_secondary", "More like eleven now, with the prices.", 0.5),
    ("client_primary", "And we still give my mom three and a half thousand a month, same as before.", 0.9),
    ("advisor", "Last thing: are you both still non-smokers?", 1.0),
    ("client_primary", "Ja, neither of us smokes.", 0.5),
    ("advisor", "Great. I'll send a summary and a few questions to close the gaps.", 0.9),
]

# Words the ASR heard poorly (confidence) and their n-best alternatives.
RISKY: dict[str, tuple[float, list[tuple[str, float]]]] = {
    "Brightwater?": (0.58, [("Bright", 0.22)]),
    "eighteen": (0.61, [("eighty", 0.24)]),
    "fifty": (0.66, [("fifteen", 0.21)]),
    "two-eighty.": (0.71, [("two-eighteen", 0.18)]),
    "four-fifty.": (0.69, [("four-fifteen", 0.2)]),
    "three-fifty,": (0.74, [("three-fifteen", 0.17)]),
    "Krugerrands.": (0.48, [("Kruger rands", 0.3), ("crew grants", 0.12)]),
    "Palesa.": (0.52, [("Pelisa", 0.25)]),
    "eleven": (0.72, [("seven", 0.16)]),
}
SECONDS_PER_WORD = 0.37


def _conf(seg: int, i: int, word: str) -> float:
    """A stable pseudo-random confidence for an ordinary word: a noisy phone line, mostly 0.62–0.95."""
    h = int(hashlib.sha256(f"{seg}:{i}:{word}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round(0.62 + 0.33 * h, 3)


def build_transcript() -> Transcript:
    segments: list[Segment] = []
    t = 0.0
    for n, (role, text, pause) in enumerate(DIALOGUE, start=1):
        t_start = round(t + pause, 2)
        words: list[Word] = []
        cursor = t_start
        for i, w in enumerate(text.split(" ")):
            dur = SECONDS_PER_WORD * (1.4 if len(w) > 8 else 1.0)
            conf, alts = RISKY.get(w, (_conf(n, i, w), []))
            words.append(Word(text=w, t_start=round(cursor, 2), t_end=round(cursor + dur - 0.04, 2), conf=conf, alts=alts))
            cursor += dur
        t_end = round(cursor, 2)
        label, role_conf = SPEAKERS[role]
        seg = Segment(
            segment_id=f"s{n:04d}",
            speaker_label=label,
            speaker_role=role,  # type: ignore[arg-type]
            role_conf=role_conf,
            diarization_conf=0.93 if role != "client_secondary" else 0.88,
            t_start=t_start,
            t_end=t_end,
            words=words,
            text=text,
        )
        segments.append(seg)
        t = t_end
    # Lerato cut in on Thabo's RA figure: the turns overlap and she corrects him.
    s19, s20 = segments[18], segments[19]
    segments[18] = s19.model_copy(update={"flags": {"crosstalk"}})
    segments[19] = s20.model_copy(update={"flags": {"crosstalk", "self_correction"}})
    for i, s in enumerate(segments):
        if s.mean_conf < 0.6:
            segments[i] = s.model_copy(update={"flags": set(s.flags) | {"low_asr"}})
    spans = [
        _span("n1", segments, "s0004", "one hundred and eighteen thousand", [(118_000, 0.97), (180_000, 0.03)], "monthly"),
        _span("n2", segments, "s0019", "three-fifty,", [(350_000, 0.93), (3_500_000, 0.07)], "once"),
        _span("n3", segments, "s0020", "two-eighty.", [(280_000, 0.95), (2_800_000, 0.05)], "once"),
        _span("n4", segments, "s0022", "eighteen fifty", [(1_850_000, 0.86), (185_000, 0.14)], "once", second_pass=1_850_000),
        _span("n5", segments, "s0025", "sixty thousand", [(60_000, 0.98)], "once"),
    ]
    tr = Transcript(
        call_id=CALL_ID,
        anchor_date=ANCHOR,
        segments=segments,
        asr_provider=VERSIONS.asr_provider,
        asr_model_version=VERSIONS.asr_model_version,
        numeric_spans=spans,
        channel_layout="mono",
    )
    return tr.model_copy(update={"quality": quality(tr)})


def _span(span_id: str, segs: list[Segment], seg_id: str, surface: str, readings: list[tuple[float, float]], period: str, second_pass: float | None = None) -> NumericSpan:
    seg = next(s for s in segs if s.segment_id == seg_id)
    texts = [w.text for w in seg.words]
    parts = surface.split(" ")
    start = next(i for i in range(len(texts)) if texts[i : i + len(parts)] == parts)
    words = seg.words[start : start + len(parts)]
    rs = [Reading(value=v, currency="ZAR", period=period, scale_source="context" if w < 0.9 else "scale word", weight=w) for v, w in readings]  # type: ignore[arg-type]
    sp = Reading(value=second_pass, currency="ZAR", period=period, scale_source="second pass", weight=1.0) if second_pass is not None else None  # type: ignore[arg-type]
    return NumericSpan(
        span_id=span_id,
        segment_id=seg_id,
        kind="amount",
        word_start=start,
        word_end=start + len(parts),
        surface=surface,
        readings=rs,
        provider_itn=f"{int(readings[0][0]):,}".replace(",", " "),
        mean_conf=round(sum(w.conf for w in words) / len(words), 3),
        second_pass_reading=sp,
        second_pass_agreement=None if sp is None else sp.value == readings[0][0],
        second_pass_method=None if sp is None else "grammar_constrained",
    )


def quality(tr: Transcript) -> QualityReport:
    words = [w for s in tr.segments for w in s.words]
    speech = sum(s.t_end - s.t_start for s in tr.segments)
    low = sum(s.t_end - s.t_start for s in tr.segments if "low_asr" in s.flags)
    cross = sum(max(0.0, a.t_end - b.t_start) for a, b in zip(tr.segments, tr.segments[1:]) if b.t_start < a.t_end)
    mean_conf = sum(w.conf for w in words) / len(words)
    banners = []
    if mean_conf < 0.8:
        banners.append(
            f"Mean word confidence is {mean_conf:.2f}, below 0.80: this was a noisy line, so amounts may be misheard "
            "and extraction confidence is capped. Check the flagged amounts against the recording."
        )
    if low / speech > 0.15:
        banners.append(f"{low / speech:.0%} of the speech was hard to hear, above the 15% limit.")
    return QualityReport(
        mean_word_conf=round(mean_conf, 3),
        low_asr_share=round(low / speech, 3),
        crosstalk_share=round(cross / speech, 3),
        n_speakers=3,
        role_conf_min=min(c for _, c in SPEAKERS.values()),
        scale_ambiguous_spans=sum(1 for s in tr.numeric_spans if len(s.readings) > 1 and s.readings[0].weight < 0.9),
        banners=banners,
        cap_p_correct=mean_conf < 0.8,
    )


def ev(tr: Transcript, seg_id: str, quote: str, role: str = "supports", score: float = 0.96) -> Evidence:
    """Evidence for a verbatim quote: character offsets in the segment, word timestamps and ASR confidence."""
    seg = tr.segment(seg_id)
    start = seg.text.index(quote)
    end = start + len(quote)
    pos, covered = 0, []
    for w in seg.words:
        w_start = seg.text.index(w.text, pos)
        w_end = w_start + len(w.text)
        pos = w_end
        if w_end > start and w_start < end:
            covered.append(w)
    return Evidence(
        segment_id=seg_id,
        char_start=start,
        char_end=end,
        quote=quote,
        speaker_role=seg.speaker_role,
        t_start=covered[0].t_start,
        t_end=covered[-1].t_end,
        asr_mean_conf=round(sum(w.conf for w in covered) / len(covered), 3),
        role=role,  # type: ignore[arg-type]
        aligner_score=score,
    )


# --------------------------------------------------------------------------- the profile


def load_prior_document() -> F.FnaDocument:
    return F.FnaDocument.model_validate(json.loads((PIPELINE / "tests" / "fixtures" / "sample_fna.json").read_text()))


NEW_ENTITIES = [
    Entity(
        entity_id="goal.kagiso_overseas_study",
        type="goal",
        canonical_name="Kagiso studying overseas",
        aliases=["study overseas", "the UK"],
        attributes={"event_kind": "education-offshore", "about": "person.kagiso_mokoena_2"},
        first_mention="s0016",
    ),
    Entity(
        entity_id="policy.group_disability_new",
        type="policy",
        canonical_name="Group disability cover (new)",
        aliases=["disability cover through work"],
        attributes={"policy_type": "disability-lump", "life_assured": "client"},
        first_mention="s0023",
        fna_list="policies",
    ),
    Entity(
        entity_id="property.krugerrands_new",
        type="property",
        canonical_name="Krugerrands",
        aliases=["Krugerrands"],
        attributes={"asset_type": "other", "owner": "client"},
        first_mention="s0025",
        fna_list="assets",
    ),
]

KAGISO = "person.kagiso_mokoena_2"
NEO = "person.neo_mokoena_1"
MMABATHO = "person.mmabatho_mokoena_3"
RA = "account.retirement_annuity_provi_2"
BOND = "liability.home_loan_1"
DISABILITY = "policy.group_disability_new"
KRUGER = "property.krugerrands_new"
STUDY = "goal.kagiso_overseas_study"
JUNE_2026 = Triangular(low=2026.414, mode=2026.455, high=2026.496)  # "in June" as a decimal-year date


class Call:
    """Collects the extraction's ops for one batch, in the order the agent emitted them."""

    def __init__(self, profile: Profile, tr: Transcript) -> None:
        self.profile = profile
        self.tr = tr
        self.catalog = load_catalog()
        self.specs: list[tuple[str, str, str | None, dict[str, Any], str]] = []

    def path(self, slot_id: str, instance: str | None) -> str:
        slot = self.catalog.slot(slot_id)
        if slot.per_person:
            return slot.fna_path_for(instance)
        if slot_id == "expenses.items[]":
            return f"expenses.items[{instance}]"
        if not slot.exported:
            return slot.fna_path.replace("[]", f"[{instance}]", 1)
        if instance and "[]" in slot.fna_path:
            return slot.fna_path_for(None, binding.row_id_for(self.profile, instance))
        return slot.fna_path

    def q(self, slot_id: str, dist: Any, basis: str = "na") -> Quantity:
        unit, period = binding.quantity_unit(self.catalog.slot(slot_id))
        return Quantity(dist=dist, unit=unit, period=period, basis=basis)  # type: ignore[arg-type]

    def refs(self, slot_id: str, instance: str | None) -> list[str]:
        if self.catalog.slot(slot_id).per_person and instance:
            return ["person.client_primary" if instance == "client" else "person.spouse"]
        return [instance] if instance and instance in self.profile.entities else []

    def add(
        self,
        slot_id: str,
        instance: str | None,
        hyps: list[tuple[Any, float, str]],
        evidence: list[Evidence],
        *,
        interpretation: str,
        status: str = "stated",
        attribution: str = "client",
        hedges: tuple[tuple[str, float], ...] = (),
        basis: str = "na",
        actor: str = "extractor",
        derivation: str | None = None,
        term_resolution: dict[str, Any] | None = None,
        normalization: dict[str, Any] | None = None,
        source: str = "call",
    ) -> str:
        fid = fact_id_for(slot_id, instance)
        payload = {
            "slot_id": slot_id,
            "instance_key": instance,
            "fna_path": self.path(slot_id, instance),
            "source": source,
            "status": status,
            "hypotheses": [Hypothesis(value=self.q(slot_id, d, basis), weight=w, rationale=r) for d, w, r in hyps],
            "evidence": evidence,
            "hedges": [Hedge(term=t, strength=s, lexicon_key=t) for t, s in hedges],
            "speaker_attribution": attribution,
            "interpretation": interpretation,
            "said": evidence[0].quote if evidence else "",
            "entity_refs": self.refs(slot_id, instance),
            "derivation": derivation,
            "term_resolution": term_resolution,
            "normalization": normalization,
            "provenance": PROV,
        }
        self.specs.append(("ADD", fid, evidence[0].segment_id if evidence else None, payload, actor))
        return fid

    def assume(self, slot_id: str, instance: str | None, value: Any) -> str:
        """A field nobody discussed on a new row: the FNA default, shown to the adviser as assumed (§4.3)."""
        fid = fact_id_for(slot_id, instance)
        payload = {
            "slot_id": slot_id,
            "instance_key": instance,
            "fna_path": self.path(slot_id, instance),
            "source": "fna_default",
            "status": "not_discussed",
            "hypotheses": [Hypothesis(value=self.q(slot_id, Point(value=value)), weight=1.0, rationale="FNA default: not discussed")],
            "entity_refs": self.refs(slot_id, instance),
            "provenance": PROV,
        }
        self.specs.append(("ADD", fid, None, payload, "loader"))
        return fid

    def op(self, kind: str, fid: str, seg: str | None, payload: dict[str, Any], actor: str = "extractor") -> None:
        self.specs.append((kind, fid, seg, payload, actor))

    def apply(self) -> Profile:
        result = apply_state_ops(self.profile, make_ops(self.profile, self.specs))
        if result.rejected:
            raise ValueError(f"rejected ops: {[(r.op.fact_id, r.reason) for r in result.rejected]}")
        self.specs = []
        self.profile = result.profile
        return self.profile


def build_profile() -> tuple[Profile, Transcript, F.FnaDocument]:
    prior = load_prior_document()
    tr = build_transcript()
    profile = binding.load_prior(prior, None, call_id=CALL_ID, run_id=RUN_ID, anchor_date=ANCHOR, provenance=PROV)
    profile = profile.model_copy(update={"entities": {**profile.entities, **{e.entity_id: e for e in NEW_ENTITIES}}})
    c = Call(profile, tr)
    E = lambda seg, quote, role="supports": ev(tr, seg, quote, role)  # noqa: E731
    # Scoring annotations the verifier, calibrator, conformal filter and VOI stage set (§9, §10):
    # fact id → (p_correct, auto_accepted, review_priority, why).
    scores: dict[str, tuple[float, bool, float, str | None]] = {}

    # Income
    fid = c.add(
        "income.{p}.grossMonthly", "client", [(Point(value=118_000), 1.0, "'one hundred and eighteen thousand gross a month'")],
        [E("s0004", "one hundred and eighteen thousand gross a month, from September"), E("s0003", "last time you were on a hundred thousand a month gross", "context")],
        interpretation="Thabo's gross salary since his promotion in September: R118,000 a month. He said 'one eighteen' and then the full figure.",
        basis="gross",
    )
    scores[fid] = (0.94, False, 0.034, "Moves Thabo's life cover and income protection from partial to shortfall.")
    fid = c.add(
        "income.{p}.grossMonthly", "spouse", [(Point(value=55_000), 1.0, "'still fifty-five a month'")],
        [E("s0006", "still fifty-five a month")],
        interpretation="Lerato's gross salary is unchanged at R55,000 a month.",
        basis="gross",
    )
    scores[fid] = (0.97, False, 0.004, "Confirms the salary on file.")
    fid = c.add(
        "income.{p}.annualBonus", "client", [(Triangular(low=108_000, mode=120_000, high=132_000), 1.0, "'about a hundred and twenty, give or take'")],
        [E("s0008", "The bonus is about a hundred and twenty, give or take, depending on how the year goes.")],
        interpretation="An annual bonus of about R120,000 that varies with the company's year; 'give or take' widens it by 10% each side.",
        hedges=(("give or take", 0.2),),
        normalization={"form": {"type": "AMOUNT_APPROX", "value": 120_000, "approx_word": "give or take"}, "lexicon_version": "1.3"},
    )
    scores[fid] = (0.83, False, 0.006, "Adds about R20,000 a year of variable income.")

    # Retirement goal
    fid = c.add(
        "retirement.{p}.retirementAge", "client", [(Triangular(low=60, mode=62, high=65), 1.0, "'between sixty and sixty-five, probably sixty-two'")],
        [E("s0010", "somewhere between sixty and sixty-five. Probably sixty-two, if the numbers allow it."), E("s0009", "Last time we planned on sixty-five for you", "context")],
        interpretation="Thabo now hopes to retire earlier than the 65 on file: anywhere from 60 to 65, most likely 62, depending on the numbers.",
        hedges=(("probably", 0.3),),
        normalization={"form": {"type": "AGE", "low": 60, "high": 65, "value": 62}, "rule": "a range with a most likely value becomes a triangular", "lexicon_version": "1.3"},
    )
    scores[fid] = (0.79, False, 0.091, "Moves the success odds by about 9 points, and between 60 and 65 his retirement need changes status.")
    fid = c.add(
        "retirement.{p}.targetPercent", "client", [(Point(value=0.7), 1.0, "Sipho proposed 70% of salary")],
        [E("s0011", "Most people in your position plan on about seventy percent of salary in retirement."), E("s0012", "If you say so. I haven't really thought about it.", "context")],
        interpretation="Sipho suggested planning on 70% of salary; Thabo did not clearly agree, so this stays the adviser's suggestion rather than his goal.",
        attribution="advisor_suggested",
    )
    scores[fid] = (0.58, False, 1.02, "An adviser suggestion on a tier A goal: the client did not clearly agree.")

    # Children and education
    fid = c.add(
        "dependants[].schoolFeesAnnual", NEO, [(Triangular(low=94_500, mode=105_000, high=115_500), 1.0, "'about a hundred and five thousand for the year'")],
        [E("s0014", "It's about a hundred and five thousand for the year now.")],
        interpretation="Neo's independent-school fees have gone up to about R105,000 a year.",
        hedges=(("about", 0.1),),
    )
    scores[fid] = (0.91, True, 0.003, "Adds about R7,000 a year to Neo's school costs.")
    fid = c.add(
        "dependants[].schoolType", KAGISO, [(Categorical(probs={"private": 0.55, "public": 0.45}), 1.0, "'thinking of moving Kagiso to the same school'")],
        [E("s0015", "we're thinking of moving Kagiso to the same school next year."), E("s0013", "Neo is still at the independent school?", "context")],
        interpretation="They are considering moving Kagiso to Neo's independent school next year; 'thinking of' is a plan, not a decision.",
        hedges=(("thinking of", 0.5),),
    )
    scores[fid] = (0.76, False, 0.012, "An independent school would cost about R60,000 a year more than her current fees.")
    fid = c.add(
        "dependants[].tertiaryCostAnnual", KAGISO,
        [
            (Triangular(low=135_000, mode=150_000, high=170_000), 0.6, "studying in South Africa: 'about a hundred and fifty a year'"),
            (Triangular(low=400_000, mode=450_000, high=520_000), 0.4, "studying in the UK: 'more like four-fifty'"),
        ],
        [E("s0017", "If she studies here it's about a hundred and fifty a year, but over there it's more like four-fifty.")],
        interpretation="Two scenarios: about R150,000 a year if Kagiso studies locally, about R450,000 a year in the UK, weighted by how likely the overseas plan is.",
        hedges=(("about", 0.1), ("more like", 0.25)),
    )
    scores[fid] = (0.71, False, 0.118, "Moves the success odds by about 12 points: the education need roughly triples if she studies overseas.")

    # The overseas study plan: a forecast-only event
    fid = c.add("event[].kind", STUDY, [(Point(value="education-offshore"), 1.0, "'study overseas'")], [E("s0016", "She might study overseas one day.")], interpretation="An overseas study plan for Kagiso.")
    scores[fid] = (0.9, False, 0.004, None)
    fid = c.add(
        "event[].description", STUDY, [(Point(value="Kagiso may study in the UK, like her cousins"), 1.0, "Lerato's words")],
        [E("s0016", "She might study overseas one day. My sister's kids all went to the UK.")],
        interpretation="Kagiso may follow her cousins to university in the UK.",
    )
    scores[fid] = (0.88, True, 0.0, None)
    fid = c.add(
        "event[].occurs", STUDY, [(Bernoulli(p=0.4), 1.0, "'might' → 40% (lexicon v1.3)")],
        [E("s0016", "She might study overseas one day.")],
        interpretation="'Might' reads as about a 40% chance that Kagiso studies overseas.",
        hedges=(("might", 0.6),),
        normalization={"form": {"type": "PROBABILITY_EVENT", "event": "study offshore", "likelihood_word": "might"}, "lexicon_version": "1.3"},
    )
    scores[fid] = (0.81, False, 0.064, "Moves the success odds by about 6 points.")
    fid = c.add(
        "event[].year", STUDY, [(Discrete(support=[2037, 2038, 2039], weights=[0.25, 0.5, 0.25]), 1.0, "Kagiso turns 19 in 2038")],
        [E("s0016", "She might study overseas one day.")],
        interpretation="'One day' is placed at Kagiso's tertiary start: she turns 19 in 2038, give or take a year.",
        status="implied",
        actor="deriver",
        derivation="event_year_from_tertiary_start_age",
    )
    scores[fid] = (0.72, False, 0.009, None)
    fid = c.add(
        "event[].cashImpact", STUDY, [(Triangular(low=-1_150_000, mode=-900_000, high=-700_000), 1.0, "three years at about R300,000 more than studying locally")],
        [E("s0017", "over there it's more like four-fifty.")],
        interpretation="Studying in the UK would cost about R300,000 a year more than locally, for three years.",
        status="implied",
        actor="deriver",
        derivation="event_cost_from_education_difference",
    )
    scores[fid] = (0.64, False, 0.041, "The extra cost of studying overseas, about R900,000 in today's money.")

    # Retirement savings: the RA value is contradicted
    fid = c.add(
        "retirementFunds[].value", RA, [(Triangular(low=315_000, mode=350_000, high=385_000), 1.0, "Thabo: 'about three-fifty, I think'")],
        [E("s0019", "The RA is sitting at about three-fifty, I think.")],
        interpretation="Thabo puts the retirement annuity at about R350,000.",
        hedges=(("about", 0.1), ("I think", 0.3)),
    )
    c.add(
        "retirementFunds[].value", RA, [(Triangular(low=266_000, mode=280_000, high=294_000), 1.0, "Lerato: 'more like two-eighty', from the statement")],
        [E("s0020", "No, it's more like two-eighty. I saw the statement last month.", "contradicts")],
        interpretation="Lerato contradicts him from last month's statement: about R280,000.",
        status="contradicted",
    )
    c.op(
        "UPDATE",
        fid,
        None,
        {
            "weights": [0.4, 0.6],
            "interpretation": "Thabo said about R350,000; Lerato, reading from last month's statement, about R280,000. "
            "Her figure carries more weight because it comes from the statement.",
        },
        "verifier",
    )
    scores[fid] = (0.46, False, 1.03, "Thabo and Lerato gave values R70,000 apart for a tier A amount.")

    # The bond: one figure, two scales
    fid = c.add(
        "liabilities[].balance", BOND,
        [(Point(value=1_850_000), 0.86, "'eighteen fifty' read as R1,850,000"), (Point(value=185_000), 0.14, "'eighteen fifty' read as R185,000")],
        [E("s0022", "We still owe about eighteen fifty on the bond.")],
        interpretation="'Eighteen fifty' most likely means R1,850,000, as on file; R185,000 is the other well-formed reading.",
        hedges=(("about", 0.1),),
        normalization={"form": {"type": "AMOUNT_APPROX", "value": 1850, "approx_word": "about"}, "scale_readings": [[1_850_000, 0.86], [185_000, 0.14]], "scale_ambiguous": True},
    )
    scores[fid] = (0.74, False, 0.31, "Read as R185,000, Thabo's life cover shortfall would fall by about R830,000.")

    # New group disability cover: a new policy row
    fid = c.add("policies[].type", DISABILITY, [(Point(value="disability-lump"), 1.0, "'disability cover through work'")], [E("s0023", "I took out disability cover through work in August.")], interpretation="New lump-sum disability cover through Thabo's employer, taken out in August.")
    scores[fid] = (0.86, False, 0.05, "A new policy: it counts towards Thabo's disability need.")
    fid = c.add("policies[].lifeAssured", DISABILITY, [(Point(value="client"), 1.0, "'I took out'")], [E("s0023", "I took out disability cover through work in August.")], interpretation="Thabo is the life assured.")
    scores[fid] = (0.9, False, 0.004, None)
    fid = c.add("policies[].isGroup", DISABILITY, [(Point(value=True), 1.0, "'through work'")], [E("s0023", "disability cover through work")], interpretation="'Through work' means an employer group scheme.", status="implied")
    scores[fid] = (0.84, False, 0.01, None)
    fid = c.add("policies[].insurer", DISABILITY, [(Point(value="Employer group scheme"), 1.0, "'through work'")], [E("s0023", "disability cover through work")], interpretation="The insurer is whoever underwrites the employer's group scheme.", status="implied")
    scores[fid] = (0.8, False, 0.001, None)
    c.op(
        "MARK_UNKNOWN",
        fact_id_for("policies[].cover", DISABILITY),
        "s0023",
        {
            "reason": "client does not know the benefit yet",
            "evidence": [E("s0023", "I don't know the amount yet.")],
            "slot_id": "policies[].cover",
            "instance_key": DISABILITY,
            "fna_path": c.path("policies[].cover", DISABILITY),
            "provenance": PROV,
        },
    )
    scores[fact_id_for("policies[].cover", DISABILITY)] = (0.88, False, 0.24, "Decides whether Thabo's disability need is covered.")
    scores[c.assume("policies[].definition", DISABILITY, "own-occupation")] = (0.5, False, 0.002, None)
    scores[c.assume("policies[].accelerated", DISABILITY, False)] = (0.5, False, 0.001, None)

    # Krugerrands: a new asset and a term nobody has resolved
    fid = c.add(
        "assets[].type", KRUGER, [(Point(value="other"), 1.0, "'Krugerrands': not in the catalogue, filed as Other")],
        [E("s0025", "I bought a few Krugerrands.")],
        interpretation="'Krugerrands' matched nothing in the catalogue or the glossary with enough confidence, so the asset is filed as Other until the term is confirmed.",
        term_resolution={
            "term": "Krugerrands",
            "status": "unresolved",
            "fallback": "other",
            "candidates": [
                {"canonical_name": "Krugerrand gold bullion coins", "product_type": "other", "provider": "", "source": "product_catalogue", "score": 0.74},
                {"canonical_name": "Krugerrand-linked exchange traded fund", "product_type": "shares", "provider": "", "source": "web:etfsa.co.za", "score": 0.38},
            ],
        },
    )
    scores[fid] = (0.69, False, 0.07, "The asset type decides whether it counts as liquid in the estate.")
    fid = c.add("assets[].description", KRUGER, [(Point(value="Krugerrands in the home safe"), 1.0, "Thabo's words")], [E("s0025", "They're in the safe at home")], interpretation="Krugerrand coins kept in the safe at home.")
    scores[fid] = (0.9, True, 0.0, None)
    fid = c.add("assets[].owner", KRUGER, [(Point(value="client"), 1.0, "'I bought'")], [E("s0025", "I bought a few Krugerrands.")], interpretation="Thabo bought them, so they are his.")
    scores[fid] = (0.82, False, 0.002, None)
    fid = c.add(
        "assets[].value", KRUGER, [(Triangular(low=42_000, mode=60_000, high=78_000), 1.0, "'maybe sixty thousand worth'")],
        [E("s0025", "maybe sixty thousand worth.")],
        interpretation="About R60,000; 'maybe' widens the range to 30% either side.",
        hedges=(("maybe", 0.6),),
    )
    scores[fid] = (0.66, False, 0.008, None)

    # Estate: new wills and guardians
    for person, prob, prio in (("client", 0.93, 0.001), ("spouse", 0.95, 0.02)):
        fid = c.add(
            "estate.{p}.hasWill", person, [(Point(value="yes"), 1.0, "'We signed new wills in June, both of us'")],
            [E("s0027", "We signed new wills in June, both of us, with the attorney.")],
            interpretation=("Lerato now has a will: they both signed new wills in June." if person == "spouse" else "Thabo signed a new will in June."),
        )
        why = "Lerato's estate no longer devolves under the Intestate Succession Act." if person == "spouse" else None
        scores[fid] = (prob, False, prio, why)
        fid = c.add("estate.{p}.willDate", person, [(JUNE_2026, 1.0, "'in June'")], [E("s0027", "We signed new wills in June")], interpretation="Signed in June 2026; the day was not given.")
        scores[fid] = (0.86, True, 0.0, None)
        fid = c.add(
            "estate.{p}.guardianNominated", person, [(Point(value="yes"), 1.0, "'Yes, my sister Palesa.'")],
            [E("s0029", "Yes, my sister Palesa."), E("s0028", "Did you name a guardian for the children?", "context")],
            interpretation="The new wills name Lerato's sister Palesa as guardian of the children.",
        )
        scores[fid] = (0.9, False, 0.002, None)

    # Budget, family support and smoking
    fid = c.add(
        "expenses.items[]", "groceries", [(Triangular(low=9_900, mode=11_000, high=12_100), 1.0, "'More like eleven now'")],
        [E("s0031", "More like eleven now, with the prices."), E("s0030", "still about nine thousand a month?", "context")],
        interpretation="Groceries are now about R11,000 a month, up from R9,000.",
        hedges=(("more like", 0.25),),
    )
    scores[fid] = (0.87, False, 0.007, "Adds R2,000 a month to essential spending.")
    fid = c.add("dependants[].monthlySupport", MMABATHO, [(Point(value=3_500), 1.0, "'three and a half thousand a month, same as before'")], [E("s0032", "three and a half thousand a month, same as before.")], interpretation="Support for Thabo's mother is unchanged at R3,500 a month.")
    scores[fid] = (0.95, True, 0.001, None)
    for person in ("client", "spouse"):
        fid = c.add("{p}.smoker", person, [(Point(value=False), 1.0, "'neither of us smokes'")], [E("s0034", "neither of us smokes.")], interpretation="Neither of them smokes.")
        scores[fid] = (0.96, True, 0.0, None)

    profile = c.apply()
    problems = check_profile(profile)
    if problems:
        raise ValueError("the fixture profile breaks the §3.5 invariants:\n" + "\n".join(problems))
    for fid, (p, auto, prio, why) in scores.items():
        voi: dict[str, Any] = {"priority": prio}
        if why:
            voi["why_text"] = why
        profile = annotate(profile, fid, p_correct=p, p_raw=round(min(0.99, p + 0.05), 2), auto_accepted=auto, review_priority=prio, voi=voi)
    profile = profile.model_copy(update={"blocking_flags": blocking_flags(c), "open_questions": followups(c)})
    return profile, tr, prior


def blocking_flags(c: Call) -> list[BlockingFlag]:
    f = fact_id_for
    return [
        BlockingFlag(flag_id="flag-ra-contradicted", kind="tier_a_contradicted", fact_id=f("retirementFunds[].value", RA), message="Thabo said about R350,000 and Lerato about R280,000 for the retirement annuity."),
        BlockingFlag(
            flag_id="flag-bond-scale",
            kind="scale_ambiguous",
            fact_id=f("liabilities[].balance", BOND),
            message="'Eighteen fifty' could be R1,850,000 or R185,000: the top reading carries 86% of the weight, below the 90% needed.",
        ),
        BlockingFlag(flag_id="flag-disability-cover", kind="tier_a_missing", fact_id=f("policies[].cover", DISABILITY), message="Thabo has new group disability cover, but the lump sum was not given."),
        BlockingFlag(
            flag_id="flag-krugerrands",
            kind="unresolved_term",
            fact_id=f("assets[].type", KRUGER),
            message="'Krugerrands' matched nothing in the catalogue or the glossary; the asset is filed as Other until you confirm the term.",
        ),
        BlockingFlag(
            flag_id="flag-retirement-age",
            kind="status_flip",
            fact_id=f("retirement.{p}.retirementAge", "client"),
            message="Retiring at 60 to 62 leaves Thabo's retirement need in shortfall; at 63 to 65 it is partial.",
        ),
        BlockingFlag(
            flag_id="flag-income-flip",
            kind="status_flip",
            fact_id=f("income.{p}.grossMonthly", "client"),
            message="At R118,000 a month, Thabo's life cover and income protection move from partial to shortfall.",
        ),
    ]


def followups(c: Call) -> list[FollowUp]:
    f = fact_id_for
    return [
        FollowUp(
            followup_id="fu-kagiso-study",
            fact_ids=[f("dependants[].tertiaryCostAnnual", KAGISO), f("event[].occurs", STUDY)],
            question="Is Kagiso more likely to study in South Africa or overseas, and what do you expect a year of study to cost in each case?",
            why="Moves the success odds by about 12 points.",
            expected_pos_change=0.118,
            answer_type="AMOUNT_RANGE",
        ),
        FollowUp(
            followup_id="fu-retirement-age",
            fact_ids=[f("retirement.{p}.retirementAge", "client")],
            question="Thabo, would you say 60, 62 or 65 is the most realistic age for you to stop working full time?",
            why="Moves the success odds by about 9 points.",
            expected_pos_change=0.091,
            answer_type="AGE",
        ),
        FollowUp(
            followup_id="fu-ra-statement",
            fact_ids=[f("retirementFunds[].value", RA)],
            question="We noted two different values for Thabo's retirement annuity. Could you send us the latest statement?",
            why="Settles a R70,000 difference on a tier A amount.",
            expected_pos_change=0.031,
            answer_type="AMOUNT_EXACT",
        ),
        FollowUp(
            followup_id="fu-disability-cover",
            fact_ids=[f("policies[].cover", DISABILITY)],
            question="What lump sum does Thabo's new group disability cover pay? The benefit schedule from HR will show it.",
            why="Decides whether Thabo's disability need is covered.",
            expected_pos_change=0.024,
            answer_type="AMOUNT_EXACT",
        ),
        FollowUp(
            followup_id="fu-retirement-income",
            fact_ids=[f("retirement.{p}.targetPercent", "client")],
            question="How much of your current salary would you like to live on in retirement: about 60%, 70% or 80%?",
            why="Replaces an adviser suggestion with the client's own goal.",
            expected_pos_change=0.018,
            answer_type="RATE",
        ),
    ]


# --------------------------------------------------------------------------- the forecast

# The FNA engine's deterministic retirement projection of the median document as at the call date
# (lib/fna/analysis.ts computeRetirement), next to which the review overlay shows the Monte Carlo
# result (§12.8). The app's tests check these against analyse() on the fixture's median document.
DETERMINISTIC: dict[str, float | None] = {
    "client.projected_capital": 31_012_251.0,
    "client.required_capital": 53_599_012.0,
    "client.depletion_age": 75.83,
    "client.replacement_ratio": 0.405,
    "spouse.projected_capital": 23_268_086.0,
    "spouse.required_capital": 33_951_827.0,
    "spouse.depletion_age": 82.08,
    "spouse.replacement_ratio": 0.514,
    # §12.8 consistency run: zero volatility, every fact at its median; must agree within 0.5%.
    "client.mc_zero_vol_capital": 30_951_000.0,
    "spouse.mc_zero_vol_capital": 23_224_000.0,
}
# Shortfall bands per need over 1,000 materialised draws (§12.7 use 2), in rand ("{need}.{person}").
NEED_BANDS: dict[str, dict[str, float]] = {
    "life.client": {"p10": 5_120_000, "p50": 5_890_000, "p90": 6_940_000},
    "life.spouse": {"p10": 4_610_000, "p50": 4_951_000, "p90": 5_330_000},
    "disability.client": {"p10": 2_880_000, "p50": 3_047_000, "p90": 3_240_000},
    "disability.spouse": {"p10": 2_230_000, "p50": 2_340_000, "p90": 2_460_000},
    "income-protection.client": {"p10": 36_000, "p50": 40_800, "p90": 46_000},
    "income-protection.spouse": {"p10": 42_500, "p50": 44_900, "p90": 47_300},
    "severe-illness.client": {"p10": 2_400_000, "p50": 2_572_000, "p90": 2_760_000},
    "severe-illness.spouse": {"p10": 1_320_000, "p50": 1_375_000, "p90": 1_430_000},
    "estate.client": {"p10": 120_000, "p50": 925_000, "p90": 1_010_000},
    "estate.spouse": {"p10": 0, "p50": 17_000, "p90": 64_000},
    "retirement.client": {"p10": 14_100_000, "p50": 22_590_000, "p90": 31_800_000},
    "retirement.spouse": {"p10": 8_900_000, "p50": 10_680_000, "p90": 12_700_000},
    "education.household": {"p10": 610_000, "p50": 810_000, "p90": 1_890_000},
    "emergency.household": {"p10": 128_000, "p50": 141_000, "p90": 156_000},
}


def net_worth_bands(ages: list[int]) -> dict[str, list[float]]:
    """Real net worth by age: savings build to a peak at 62, then fund retirement; the spread widens with time."""
    z = {"p10": -1.2816, "p25": -0.6745, "p50": 0.0, "p75": 0.6745, "p90": 1.2816}
    peak = 4_800_000 * 1.03**22 + 280_000 * 22
    bands: dict[str, list[float]] = {k: [] for k in z}
    for age in ages:
        t = age - ages[0]
        median = 4_800_000 * 1.03**t + 280_000 * t if age <= 62 else max(0.0, peak - 430_000 * (age - 62))
        sigma = 0.11 * math.sqrt(max(t, 0.5))
        for k, zk in z.items():
            bands[k].append(float(round(median * math.exp(zk * sigma), -3)))
    return bands


def preview_forecast(profile: Profile) -> ForecastResult:
    ages = list(range(40, 101))
    return ForecastResult(
        run_id=RUN_ID,
        profile_version=str(profile.version),
        config_version=VERSIONS.forecast_config_version,
        cma_version=VERSIONS.cma_version,
        n_paths=10_000,
        seed=20261015,
        pos=0.71,
        pos_ci=(0.701, 0.719),
        goal_funding={"goals[].cost#goal.family_holiday_overseas_1": 0.83, "goals[].cost#goal.replace_polo_cash_deposi_2": 0.64},
        ages=ages,
        net_worth_bands=net_worth_bands(ages),
        shortfall={"p_any": 0.29, "median_first_shortfall_age": 84.0, "p90_total_real_shortfall": 2_400_000.0},
        liquidity_breach_prob=0.07,
        retirement_income_bands={},
        variance_attribution={"market": 0.58, "client_content": 0.27, "extraction": 0.11, "interaction": 0.04},
        market_attribution={"za_equity": 0.31, "global_equity": 0.14, "inflation": 0.08, "za_bonds": 0.03, "fx_usdzar": 0.02},
        top_drivers=[
            (fact_id_for("dependants[].tertiaryCostAnnual", KAGISO), 0.12),
            (fact_id_for("retirement.{p}.retirementAge", "client"), 0.11),
            (fact_id_for("event[].occurs", STUDY), 0.07),
            (fact_id_for("retirementFunds[].value", RA), 0.04),
            (fact_id_for("retirement.{p}.targetPercent", "client"), 0.03),
        ],
        deterministic=DETERMINISTIC,
        need_bands=NEED_BANDS,
    )


def attribution_text(f: ForecastResult) -> str:
    va = f.variance_attribution
    return (
        f"Of the uncertainty in this plan, about {va['market']:.0%} is markets, {va['client_content']:.0%} is what the client "
        f"hasn't decided, {va['extraction']:.0%} is facts we may have misheard."
    )


# --------------------------------------------------------------------------- the package


def build_package() -> dict[str, Any]:
    profile, tr, prior = build_profile()
    median = binding.materialise(profile, "median", base=prior)
    forecast = preview_forecast(profile)
    bundle = ReviewBundle(
        call_id=CALL_ID,
        run_id=RUN_ID,
        profile_version=profile.version,
        state=RunState.AWAITING_REVIEW,
        median_document=median,
        prior_document=prior,
        sidecar=binding.sidecar(profile),
        transcript=tr,
        audio_url=None,
        entities=dict(sorted(profile.entities.items())),
        followups=profile.open_questions,
        blocking_flags=profile.blocking_flags,
        preview_forecast=forecast,
        deterministic_analysis=None,
        quality=tr.quality,
        spot_check=False,
        versions=VERSIONS,
        attribution_text=attribution_text(forecast),
    )
    return ReviewPackage(bundle=bundle).model_dump(mode="json")


def render(pkg: dict[str, Any], compact: bool = False) -> str:
    if compact:
        return json.dumps(pkg, separators=(",", ":"), ensure_ascii=False) + "\n"
    return json.dumps(pkg, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    pkg = build_package()
    texts = {path: render(pkg, compact) for path, compact in OUTPUTS}
    stale = [p for p, text in texts.items() if not p.exists() or p.read_text() != text]
    if "--check" in argv:
        for p in stale:
            print(f"stale: {p.relative_to(REPO)} (run scripts/make_review_fixture.py)", file=sys.stderr)
        return 1 if stale else 0
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texts[p])
    print("up to date" if not stale else "wrote " + ", ".join(f"{p.relative_to(REPO)} ({len(texts[p]) // 1024} KB)" for p in stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
