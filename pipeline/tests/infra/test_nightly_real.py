"""The nightly activities over the merged verification code: labels from a real review, the calibrator's own retrain
decision and the conformal refit, stored and read back the way the agent activity loads them (spec §9.3–9.5, §11.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from advisor_intel.activities import NightlyActivities, StageFunctions
from advisor_intel.config import Settings
from advisor_intel.extract.reducer import make_ops
from advisor_intel.models.runs import ReviewSubmission
from advisor_intel.workflows import contracts as C

from .factories import GROSS, RET_AGE
from .test_activities import NOW, RUN, _through_forecast, env

pytestmark = pytest.mark.postgres

verify = pytest.importorskip("advisor_intel.verify.labels")


async def test_labels_retrain_and_refit_with_the_verification_code(ctx):
    await _through_forecast(ctx)
    profile = await ctx.store.load_profile(RUN)
    ops = make_ops(profile, [("CONFIRM", RET_AGE, None, {}, "reviewer"), ("RETRACT", GROSS, None, {"reason": "misheard"}, "reviewer")])
    review = ReviewSubmission(call_id="call-1", reviewer_id="adv-1", profile_version=profile.version, submitted_at=NOW, ops=ops)
    await env().run(ctx.acts.apply_review, C.ReviewInput(run_id=RUN, review=review))
    nightly = NightlyActivities(store=ctx.store, objects=ctx.objects, settings=Settings(), stages=StageFunctions())
    window = C.NightlyWindow(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    e = env("nightly")
    labels = await e.run(nightly.build_calibration_labels, window)
    assert labels.n_labels == 2 and labels.n_calls == 1 and labels.labels_uri
    rows = [verify.Label.model_validate_json(line) for line in (await ctx.objects.get_bytes(labels.labels_uri)).decode().splitlines()]
    assert {(lb.fact_id, lb.correct, lb.action) for lb in rows} == {(RET_AGE, 1, "accepted"), (GROSS, 0, "rejected")}
    cal = await e.run(nightly.retrain_calibrator, labels)
    conf = await e.run(nightly.refit_conformal, cal)
    if cal.artifact_uri:  # a candidate was fitted: it round-trips through object storage
        from advisor_intel.verify.calibrator import Calibrator

        assert Calibrator.from_bytes(await ctx.objects.get_bytes(cal.artifact_uri)).version == cal.version
    else:
        assert cal.skipped_reason
    # One reviewed call is far below the 50 calibration calls conformal auto-accept needs (§9.5).
    assert not conf.auto_accept_enabled and conf.skipped_reason and conf.thresholds_uri
    from advisor_intel.verify.conformal import ConformalThresholds

    th = ConformalThresholds.model_validate(await ctx.objects.get_json(conf.thresholds_uri))
    assert not th.enabled and th.calibrator_version == conf.calibrator_version
