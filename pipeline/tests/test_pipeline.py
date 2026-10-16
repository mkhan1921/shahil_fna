"""The in-process runner (advisor_intel.pipeline): a scripted call through every installed stage."""

from __future__ import annotations

from datetime import date

import pytest

from advisor_intel import llm, stats
from advisor_intel.models.runs import VersionPins
from advisor_intel.pipeline import CallInput, process_call
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript

SCRIPT = "\n".join(f"{role}: {text}" for role, text in SAMPLE_CALL)
ROLES = {"advisor": "advisor", "client_primary": "client_primary", "client_secondary": "client_secondary"}


@pytest.fixture
async def oracle_pins():
    t = await ingested_transcript(SAMPLE_CALL, call_id="call-pipe")  # the oracle quotes the same segments
    oracle = Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD)
    yield VersionPins(reasoning_model=llm.register_local_model("oracle-pipe", scripted_model(oracle.handler)))
    llm.unregister_local_model("oracle-pipe")


async def test_scripted_call_runs_every_installed_stage(oracle_pins):
    res = await process_call(CallInput(call_id="call-pipe", anchor_date=date(2026, 10, 15), script=SCRIPT, script_roles=ROLES), oracle_pins)
    assert not res.partial and res.transcript.numeric_spans
    assert res.tool_calls[0] == "make_plan" and "extract_slot_group" in res.tool_calls
    doc = res.document
    assert doc.client.smoker is False and doc.spouse.smoker is False
    assert 52_000 < doc.income["client"].grossMonthly < 66_000 and doc.income["spouse"].grossMonthly == 35_000
    assert {d.name for d in doc.dependants} == {"Thandi", "Lwazi"}
    assert sorted(f.value for f in doc.retirementFunds if f.value) == [300_000, 1_200_000]
    assert any(p.cover == 3_000_000 for p in doc.policies)
    fields = res.sidecar["fields"]
    assert res.sidecar["format"] == "advisor-intel-sidecar" and fields["income.spouse.grossMonthly"]["evidence"]
    gross = fields["income.client.grossMonthly"]
    assert gross["status"] == "implied" and gross["derivation"] == "gross_from_net"
    assert fields["income.client.annualBonus"]["derivation"] == "gross_from_net"
    assert res.run_id.startswith("run-call-pipe-")
    assert "verify" not in res.skipped and "voi" not in res.skipped
    assert {"verify_facts", "check_constraints", "run_sensitivity", "draft_followups"} <= set(res.tool_calls)
    open_qs = [q for q in res.followups if q.status == "open"]
    assert open_qs and all(q.question and q.fact_ids for q in open_qs)
    missing = {f.fact_id for f in res.flags if f.kind == "tier_a_missing"}
    assert "household.maritalStatus" in missing and "household.includeSpouse" not in missing  # the spouse spoke on the call
    call_facts = [f for f in res.profile.facts.values() if f.source == "call"]
    assert all(0.0 < f.p_correct < 1.0 and not f.auto_accepted for f in call_facts)  # cold start: nothing auto-accepted


def test_cli_writes_the_files_the_app_imports(tmp_path, oracle_pins, monkeypatch):
    import json

    from advisor_intel import pipeline as P

    script = tmp_path / "call.txt"
    script.write_text(SCRIPT)
    monkeypatch.setattr(P, "deployment_pins", lambda settings=None: oracle_pins)
    out = tmp_path / "out"
    assert P.main(["--script", str(script), "--roles", ",".join(f"{k}={v}" for k, v in ROLES.items()), "--date", "2026-10-15",
                   "--call-id", "call-pipe", "--out", str(out)]) == 0
    export = json.loads((out / "export.json").read_text())
    assert export["format"] == "shahil-fna" and export["document"]["income"]["spouse"]["grossMonthly"] == 35_000
    pkg = json.loads((out / "review_package.json").read_text())
    assert pkg["format"] == "advisor-intel-review" and pkg["bundle"]["state"] == "AWAITING_REVIEW"
    assert pkg["bundle"]["sidecar"]["fields"]["income.spouse.grossMonthly"]["status"] == "stated"
    assert json.loads((out / "sidecar.json").read_text())["format"] == "advisor-intel-sidecar"
