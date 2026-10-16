"""The review overlay's contracts, from the pipeline's side (spec §11.5, §15.2).

The FNA app's overlay is written against JSON Schemas exported from these models and against two
fixtures: the hand-built demo package and a package from ``process_call``. Here the pipeline checks
its half: the sidecar model describes what ``binding.sidecar()`` emits, the exported schemas and
fixtures are current, and what the app sends back — a review result and a gold annotation, both
written by the app's own tests — is accepted: the review's ops replay through the reducer onto the
same profile and materialise to exactly the reviewed client file the app saved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from advisor_intel import binding
from advisor_intel.evals.annotation import GoldAnnotation
from advisor_intel.extract.reducer import apply_state_ops
from advisor_intel.models.review import ReviewPackage, ReviewResult
from advisor_intel.models.sidecar import Sidecar
from advisor_intel.schema.slots import load_catalog

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parent
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(PIPELINE / "scripts"))

import export_review_schemas  # noqa: E402
import make_pipeline_review_package  # noqa: E402
import make_review_fixture  # noqa: E402


@pytest.fixture(scope="module")
def demo():
    """The demo call's profile, transcript and the prior file, as the fixture script builds them."""
    return make_review_fixture.build_profile()


def test_the_sidecar_model_describes_what_sidecar_emits(demo):
    profile, _, _ = demo
    side = Sidecar.model_validate(binding.sidecar(profile))
    assert side.last_seq == profile.ops_log[-1].seq
    assert all(f.quantity is not None for f in side.fields.values() if f.value is not None)
    derived = {f.derivation for f in side.fields.values() if f.derivation}
    assert {"gross_from_net", "horizon_from_retirement", "emergency_from_cash_flow"} <= derived


def test_the_exported_schemas_are_current():
    for name, doc in export_review_schemas.documents().items():
        assert (export_review_schemas.OUT / name).read_text() == export_review_schemas.render(doc), f"{name} is stale: run scripts/export_review_schemas.py"


def test_the_demo_package_is_current_and_valid():
    pkg = make_review_fixture.build_package()
    for path, compact in make_review_fixture.OUTPUTS:
        assert path.read_text() == make_review_fixture.render(pkg, compact), f"{path.relative_to(REPO)} is stale: run scripts/make_review_fixture.py"
    bundle = ReviewPackage.model_validate(pkg).bundle
    Sidecar.model_validate(bundle.sidecar)
    assert bundle.sidecar["last_seq"] > 0 and bundle.prior_document is not None


async def test_the_pipeline_package_is_what_process_call_produces():
    pkg = await make_pipeline_review_package.build()
    assert make_pipeline_review_package.OUTPUT.read_text() == make_pipeline_review_package.render(pkg), "run scripts/make_pipeline_review_package.py"
    side = Sidecar.model_validate(pkg.bundle.sidecar)
    assert side.fields["income.client.grossMonthly"].derivation == "gross_from_net"


def test_the_apps_review_result_replays_onto_the_profile(demo):
    profile, _, prior = demo
    result = ReviewResult.model_validate_json((FIXTURES / "review_result.json").read_text())
    assert (result.call_id, result.run_id, result.base_profile_version) == (profile.call_id, profile.run_id, profile.version)
    assert result.submitted and result.ops and all(op.actor == "reviewer" for op in result.ops)
    replay = apply_state_ops(profile, result.ops)
    assert not replay.rejected, [(r.op.seq, r.op.fact_id, r.reason) for r in replay.rejected]
    facts = replay.profile.facts
    assert facts["income.{p}.grossMonthly#client"].review.status == "accepted"
    assert facts["assets[].value#property.krugerrands_new"].review.status == "rejected"
    assert facts["dependants[].schoolFeesAnnual#person.kagiso_mokoena_2"].source == "reviewer"
    assert {c.flag_id for c in result.flag_clearances} == {f.flag_id for f in profile.blocking_flags}
    assert [e.action for e in result.followup_edits] == ["reword", "drop", "add"]
    assert result.term_confirmations[0].canonical_name == "Krugerrand gold bullion coins"


def test_the_replayed_profile_materialises_to_the_file_the_app_saved(demo):
    profile, _, prior = demo
    result = ReviewResult.model_validate_json((FIXTURES / "review_result.json").read_text())
    reviewed = binding.materialise(apply_state_ops(profile, result.ops).profile, "median", base=prior, catalog=load_catalog(profile.schema_version))
    got = reviewed.model_dump(mode="json")
    saved = json.loads((FIXTURES / "reviewed_document.json").read_text())
    for doc in (got, saved):
        doc.pop("createdAt")
        doc.pop("updatedAt")
    assert got == saved


def test_the_apps_gold_annotation_is_accepted_by_the_evals_model():
    gold = GoldAnnotation.model_validate_json((FIXTURES / "gold_annotation.json").read_text())
    assert gold.call_id == make_review_fixture.CALL_ID and gold.completed_at is not None
    ranges = [f for f in gold.facts if f.value is not None and f.value.value.kind == "range"]
    assert ranges and ranges[0].value.quantiles() == (60, 62, 65)
