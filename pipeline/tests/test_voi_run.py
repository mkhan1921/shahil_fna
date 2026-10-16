"""run_voi_and_followups end to end: the real FNA engine, a stub forecast and a scripted drafting model (§10)."""

from __future__ import annotations

import functools
import json
import re
import threading
from datetime import date

from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel import binding
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import annotate, apply_state_ops, check_profile, make_ops
from advisor_intel.fna_engine.analysis import analyse as fna_analyse
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Triangular
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.profile import FollowUp
from advisor_intel.models.runs import VersionPins
from advisor_intel.voi.followups import followup_agent
from advisor_intel.voi.run import run_voi_and_followups

from .conftest import load_fixture
from .verify_factories import CATALOG, PROV, ProfileBuilder, ev, money, qty, segment, transcript

ANCHOR = date(2026, 10, 16)


def _profile():
    doc = F.FnaDocument.model_validate(load_fixture("sample_fna.json"))
    prof = binding.load_prior(doc, None, call_id="c1", run_id="wf-1", anchor_date=ANCHOR)
    facts = [
        (
            "income.{p}.grossMonthly",
            "client",
            "income.client.grossMonthly",
            qty(Triangular(low=60000, mode=100000, high=140000), "ZAR", "monthly"),
        ),
        ("retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", qty(Triangular(low=55, mode=60, high=68), "age")),
    ]
    specs = [
        (
            "ADD",
            f"{s}#{i}",
            "s0001",
            {
                "slot_id": s,
                "instance_key": i,
                "fna_path": path,
                "status": "stated",
                "hypotheses": [Hypothesis(value=q, weight=1, rationale="said")],
                "evidence": [ev("s0001", "x")],
                "provenance": PROV,
            },
            "extractor",
        )
        for s, i, path, q in facts
    ]
    prof = apply_state_ops(prof, make_ops(prof, specs)).profile
    for fid, f in prof.facts.items():
        prof = annotate(prof, fid, p_correct=0.7 if f.source == "call" else 1.0)
    answered = FollowUp(
        followup_id="fu_old",
        fact_ids=["estate.{p}.hasWill#client"],
        question="Do you have a will?",
        why="w",
        expected_pos_change=0.1,
        answer_type="CATEGORY",
        status="answered",
    )
    return prof.model_copy(update={"open_questions": [answered]}), doc


def _pos_runs(profile, fact_ids, *, n_paths, seed):
    out = {}
    for fid in fact_ids:
        wide = fid in ("income.{p}.grossMonthly#client", "retirement.{p}.retirementAge#client")
        out[fid] = {"pos_base": 0.7, "pos_q10": 0.62 if wide else 0.7, "pos_q90": 0.79 if wide else 0.7, "pos_prior": 0.6 if wide else 0.7}
    return out


def _drafter():
    def fn(messages, info: AgentInfo):
        prompt = str(messages[-1].parts[-1].content)
        items = re.findall(r"^- (i\d+): .*Answer forms: ([A-Z_]+)", prompt, flags=re.MULTILINE)
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "followups": [
                                {
                                    "item_ids": [i],
                                    "question": f"Roughly where would you put item {i}: between A and B?",
                                    "answer_type": form,
                                }
                                for i, form in items
                            ]
                        }
                    )
                )
            ]
        )

    return FunctionModel(fn)


async def test_stage6_end_to_end():
    prof, doc = _profile()
    t = transcript([segment("s0001", "We spoke about income and retiring", t0=0)])
    with followup_agent.override(model=_drafter()):
        out = await run_voi_and_followups(
            prof,
            catalog=CATALOG,
            pins=VersionPins(),
            pos_runs=_pos_runs,
            analyse=functools.partial(fna_analyse, today=ANCHOR),
            transcript=t,
            settings=Settings(),
            base_document=doc,
        )
    pr = [it.priority for it in out.priorities]
    assert pr == sorted(pr, reverse=True)
    top = out.priorities[0]
    assert top.fact_id in ("income.{p}.grossMonthly#client", "retirement.{p}.retirementAge#client") and top.forced == 1.0  # flips a need
    inc = out.profile.facts["income.{p}.grossMonthly#client"]
    assert inc.voi["why_text"].startswith("flips the") and inc.review_priority == next(
        i.priority for i in out.priorities if i.fact_id == inc.fact_id
    )
    assert inc.voi["I_extract"] == 0.3 * 0.1  # (1 − p_correct)·|PoS(f) − PoS(prior)|
    assert 0 < len(out.drafted) <= 8 and out.drafted_by == "model"
    assert len(out.review_list) <= 15
    assert out.followups == out.profile.open_questions  # what a caller writes back keeps the answered one
    statuses = {q.followup_id: q.status for q in out.followups}
    assert statuses["fu_old"] == "answered" and sum(1 for s in statuses.values() if s == "open") == len(out.drafted)
    kinds = {f.kind for f in out.flags}
    assert "status_flip" in kinds and all(not f.cleared for f in out.flags)
    assert out.profile.blocking_flags == out.flags
    assert check_profile(out.profile) == []


async def test_settings_override_the_default_weights_and_caps():
    class Tuned(Settings):
        voi_w_content: float = 0.0
        voi_w_extract: float = 0.0
        max_followups: int = 2

    prof, doc = _profile()
    with followup_agent.override(model=_drafter()):
        out = await run_voi_and_followups(
            prof,
            catalog=CATALOG,
            pins=VersionPins(),
            pos_runs=_pos_runs,
            analyse=functools.partial(fna_analyse, today=ANCHOR),
            transcript=None,
            settings=Tuned(),
            base_document=doc,
        )
    assert all(it.priority == it.forced for it in out.priorities)
    assert len(out.drafted) <= 2


class ForecastRow(BaseModel):
    """The shape of ``forecast.sensitivity.SensitivityRow``: the ends are ``pos_low`` / ``pos_high``."""

    pos_base: float
    pos_low: float
    pos_high: float
    pos_prior: float | None = None


async def test_the_runner_call_binds_today_and_reads_forecast_rows():
    prof, doc = _profile()
    seen: dict[str, object] = {}

    def sensitivity_runs(profile, fact_ids, *, n_paths=1000, seed, base_document=None):
        seen.update(base_document=base_document, seed=seed, thread=threading.current_thread() is threading.main_thread())
        return {fid: ForecastRow(pos_base=0.7, pos_low=0.6, pos_high=0.8, pos_prior=0.5) for fid in fact_ids}

    def analyse(document, *, today=None):
        seen["today"] = today
        return fna_analyse(document, today=today)

    with followup_agent.override(model=_drafter()):
        out = await run_voi_and_followups(
            prof,
            catalog=CATALOG,
            pins=VersionPins(),
            pos_runs=sensitivity_runs,
            analyse=analyse,
            transcript=None,
            base_document=doc,
            seed=7,
        )
    assert seen == {"base_document": doc, "seed": 7, "thread": False, "today": ANCHOR}
    inc = next(r for r in out.sensitivity if r.fact_id == "income.{p}.grossMonthly#client")
    assert round(inc.i_pos, 9) == 0.2 and round(inc.i_extract, 9) == 0.06
    assert out.errors == []


async def test_placeholders_are_scored_as_seeds_and_carry_their_priority():
    """A field a flag or question names but no fact holds gets a placeholder: scored like every seed (p_correct 1, never
    auto-accepted) and annotated with its row's priority and why line, so the sidecar does not show a 50% score and a
    zero priority on a field the review asks about."""
    b = ProfileBuilder()
    b.add("income.{p}.grossMonthly", "client", money(45000))  # the only fact: every other tier A field is missing
    prof = b.build()
    with followup_agent.override(model=_drafter()):
        out = await run_voi_and_followups(
            prof,
            catalog=CATALOG,
            pins=VersionPins(),
            pos_runs=None,
            analyse=functools.partial(fna_analyse, today=ANCHOR),
            transcript=None,
            settings=Settings(),
            base_document=None,
        )
    named = {f.fact_id for f in out.flags if f.fact_id} | {fid for q in out.followups for fid in q.fact_ids}
    seeds = [f for fid, f in out.profile.facts.items() if fid in named and f.source == "fna_default"]
    assert seeds and any(f.review_priority > 0 for f in seeds)  # missing tier A fields, ranked by their prior's spread
    by_id = {it.fact_id: it for it in out.priorities}
    for f in seeds:
        assert f.p_correct == 1.0 and f.p_raw == 1.0 and not f.auto_accepted and f.status == "not_discussed" and f.value is None
        if f.fact_id in by_id:
            assert f.review_priority == by_id[f.fact_id].priority and f.voi["why_text"] == by_id[f.fact_id].why
    side = binding.sidecar(out.profile, catalog=CATALOG)
    assert all(v["p_correct"] == 1.0 for v in side["fields"].values() if v["fact_id"] in {f.fact_id for f in seeds})
    assert check_profile(out.profile) == []


async def test_a_failed_view_is_reported_on_the_output():
    prof, doc = _profile()

    def broken(profile, fact_ids, **kw):
        raise RuntimeError("forecast down")

    with followup_agent.override(model=_drafter()):
        out = await run_voi_and_followups(
            prof, catalog=CATALOG, pins=VersionPins(), pos_runs=broken, analyse=functools.partial(fna_analyse, today=ANCHOR), base_document=doc
        )
    assert out.errors == ["PoS runs failed: RuntimeError: forecast down"]
    assert all(r.i_pos == 0 for r in out.sensitivity) and any(r.i_fna > 0 for r in out.sensitivity)  # the FNA view still ranked
