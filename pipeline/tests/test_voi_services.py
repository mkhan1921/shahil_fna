"""The Stage 5–6 services behind the orchestrator's tools (§13.3), on a minimal stand-in for the harness deps."""

from __future__ import annotations

import functools
import json
import re
from datetime import date
from types import SimpleNamespace

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.fna_engine.analysis import analyse as fna_analyse
from advisor_intel.harness.deps import ServiceResult
from advisor_intel.harness.services import default_services
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Triangular
from advisor_intel.models.entities import DiscourseOutput
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.runs import VersionPins
from advisor_intel.verify.consistency import ProbeResult
from advisor_intel.verify.entailment import entailment_agent
from advisor_intel.verify.services import constraints_service, verify_service
from advisor_intel.voi.followups import followup_agent
from advisor_intel.voi.services import bind_today, followups_service, sensitivity_service

from .conftest import load_fixture
from .verify_factories import CATALOG, PROV, ev, point, qty, segment, transcript

ANCHOR = date(2026, 10, 16)


def _deps():
    doc = F.FnaDocument.model_validate(load_fixture("sample_fna.json"))
    prof = binding.load_prior(doc, None, call_id="c1", run_id="wf-1", anchor_date=ANCHOR)
    q = qty(Triangular(low=55, mode=60, high=68), "age")
    spec = (
        "ADD",
        "retirement.{p}.retirementAge#client",
        "s0001",
        {
            "slot_id": "retirement.{p}.retirementAge",
            "instance_key": "client",
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "hypotheses": [Hypothesis(value=q, weight=1, rationale="said")],
            "evidence": [ev("s0001", "around sixty")],
            "provenance": PROV,
        },
        "extractor",
    )
    prof = apply_state_ops(prof, make_ops(prof, [spec])).profile
    t = transcript([segment("s0001", "I'd like to retire around sixty", t0=0)])
    return SimpleNamespace(
        catalog=CATALOG,
        transcript=t,
        pins=VersionPins(),
        discourse=DiscourseOutput(entities=prof.entities),
        profile=prof,
        inputs=SimpleNamespace(run_id="wf-1", base_document=doc),
        state=SimpleNamespace(sensitivity=[]),
    )


async def test_verify_and_constraints_services():
    deps = _deps()

    async def reextract(fact, variant):
        return ProbeResult(status="stated", value=point(60, "age"))

    def verdicts(messages, info: AgentInfo):
        claims = re.findall(r"^- (c\d+): ", str(messages[-1].parts[-1].content), flags=re.M)
        return ModelResponse(
            parts=[TextPart(json.dumps({"verdicts": [{"claim_id": c, "verdict": "entailed", "reason": "r"} for c in claims]}))]
        )

    with entailment_agent.override(model=FunctionModel(verdicts)):
        res = await verify_service(reextract=reextract)(deps.profile, ["retirement.{p}.retirementAge#client"], deps)
    assert isinstance(res, ServiceResult) and res.data["verified"] == 1
    assert res.data["results"]["retirement.{p}.retirementAge#client"]["entailment"] == "entailed"
    assert res.profile.facts["retirement.{p}.retirementAge#client"].verification["consistency"] == 1.0
    con = await constraints_service()(res.profile, deps)
    assert isinstance(con.data, list) and con.profile.facts["retirement.{p}.retirementAge#client"].verification["constraint"] in (
        "pass",
        "soft",
    )


async def test_sensitivity_then_followups_services_share_the_table():
    deps = _deps()
    run = sensitivity_service(pos_runs=None, analyse=fna_analyse)
    res = await run(deps.profile, deps)
    assert res.data and res.data[0]["priority"] >= res.data[-1]["priority"]
    assert res.profile.facts["retirement.{p}.retirementAge#client"].voi["why_text"]
    deps.state.sensitivity = res.data

    def drafts(messages, info: AgentInfo):
        items = re.findall(r"^- (i\d+): .*Answer forms: ([A-Z_]+)", str(messages[-1].parts[-1].content), flags=re.M)
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {"followups": [{"item_ids": [i], "question": "Would you say 60 or 65?", "answer_type": f} for i, f in items]}
                    )
                )
            ]
        )

    with followup_agent.override(model=FunctionModel(drafts)):
        fu = await followups_service()(res.profile, deps)
    assert fu.data and len(fu.data) <= 8 and all(q.status == "open" for q in fu.profile.open_questions)


def test_services_plug_into_the_harness_and_today_is_bound():
    services = default_services(
        verify=verify_service(reextract_factory=None),
        constraints=constraints_service(),
        sensitivity=sensitivity_service(pos_runs=None, analyse=fna_analyse),
        followups=followups_service(),
    )
    assert services.verify and services.constraints and services.sensitivity and services.followups
    prof = _deps().profile
    bound = bind_today(fna_analyse, prof)
    assert isinstance(bound, functools.partial) and bound.keywords == {"today": ANCHOR}
    plain = bind_today(lambda doc: doc, prof)
    assert not isinstance(plain, functools.partial)
