"""The in-process runner (advisor_intel.pipeline): a scripted call through every installed stage."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from advisor_intel import llm
from advisor_intel import pipeline as P
from advisor_intel.models.runs import VersionPins
from advisor_intel.pipeline import CallInput, process_call
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript

SCRIPT = "\n".join(f"{role}: {text}" for role, text in SAMPLE_CALL)
ROLES = {"advisor": "advisor", "client_primary": "client_primary", "client_secondary": "client_secondary"}


@pytest.fixture(scope="module")
def run():
    """One call through every stage, shared by the tests below (the forecast and VOI make it the slow part)."""

    async def go():
        t = await ingested_transcript(SAMPLE_CALL, call_id="call-pipe")  # the oracle quotes the same segments
        pins = VersionPins(reasoning_model=llm.register_local_model("oracle-pipe", scripted_model(Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD).handler)))
        res = await process_call(CallInput(call_id="call-pipe", anchor_date=date(2026, 10, 15), script=SCRIPT, script_roles=ROLES), pins)
        return pins, res

    try:
        yield asyncio.run(go())
    finally:
        llm.unregister_local_model("oracle-pipe")


def test_scripted_call_runs_every_installed_stage(run):
    _, res = run
    assert not res.partial and res.transcript.numeric_spans
    assert {"verify", "voi", "forecast"}.isdisjoint(res.skipped), res.skipped
    assert res.tool_calls[0] == "make_plan" and {"extract_slot_group", "verify_facts", "check_constraints", "run_sensitivity", "draft_followups"} <= set(res.tool_calls)
    assert res.run_id.startswith("run-call-pipe-")


def test_the_median_document_holds_what_the_call_established(run):
    doc = run[1].document
    assert doc.client.smoker is False and doc.spouse.smoker is False
    assert 52_000 < doc.income["client"].grossMonthly < 66_000 and doc.income["spouse"].grossMonthly == 35_000
    assert {d.name for d in doc.dependants} == {"Thandi", "Lwazi"}
    assert sorted(f.value for f in doc.retirementFunds if f.value) == [300_000, 1_200_000]
    assert any(p.cover == 3_000_000 for p in doc.policies)
    assert doc.household.includeSpouse and doc.household.maritalStatus == "married"  # the spouse spoke on the call


def test_sidecar_carries_evidence_and_derivations(run):
    res = run[1]
    fields = res.sidecar["fields"]
    assert res.sidecar["format"] == "advisor-intel-sidecar" and fields["income.spouse.grossMonthly"]["evidence"]
    gross = fields["income.client.grossMonthly"]
    assert gross["status"] == "implied" and gross["derivation"] == "gross_from_net"
    assert fields["income.client.annualBonus"]["derivation"] == "gross_from_net"


def test_verification_voi_and_the_forecast_ran(run):
    res = run[1]
    open_qs = [q for q in res.followups if q.status == "open"]
    assert open_qs and all(q.question and q.fact_ids for q in open_qs)
    missing = {f.fact_id for f in res.flags if f.kind == "tier_a_missing"}
    assert not {"household.maritalStatus", "household.includeSpouse"} & missing
    assert "household.maritalRegime" in missing  # never discussed
    call_facts = [f for f in res.profile.facts.values() if f.source == "call"]
    assert all(0.0 < f.p_correct < 1.0 and not f.auto_accepted for f in call_facts)  # cold start: nothing auto-accepted
    assert res.forecast is not None and 0.0 <= res.forecast.pos <= 1.0
    assert any(float(f.voi.get("I_pos", 0.0)) > 0 for f in res.profile.facts.values())  # the forecast's sensitivity runs fed VOI
    assert res.analysis and "table" in res.analysis


def test_cli_writes_the_files_the_app_imports(tmp_path, run, monkeypatch):
    pins, res = run
    script = tmp_path / "call.txt"
    script.write_text(SCRIPT)
    seen = {}

    async def cached(call, p, **kw):  # the run above stands in for processing the call again
        seen.update(call=call, pins=p)
        return res

    monkeypatch.setattr(P, "process_call", cached)
    monkeypatch.setattr(P, "deployment_pins", lambda settings=None: pins)
    out = tmp_path / "out"
    assert P.main(["--script", str(script), "--roles", ",".join(f"{k}={v}" for k, v in ROLES.items()), "--date", "2026-10-15",
                   "--call-id", "call-pipe", "--out", str(out)]) == 0
    assert seen["call"].script_roles == ROLES and seen["call"].anchor_date == date(2026, 10, 15) and seen["pins"] is pins
    export = json.loads((out / "export.json").read_text())
    assert export["format"] == "shahil-fna" and export["document"]["income"]["spouse"]["grossMonthly"] == 35_000
    pkg = json.loads((out / "review_package.json").read_text())
    assert pkg["format"] == "advisor-intel-review" and pkg["bundle"]["state"] == "AWAITING_REVIEW"
    assert pkg["bundle"]["sidecar"]["fields"]["income.spouse.grossMonthly"]["status"] == "stated"
    assert pkg["bundle"]["preview_forecast"] is not None
    assert json.loads((out / "sidecar.json").read_text())["format"] == "advisor-intel-sidecar"
