"""A review package from the real pipeline (spec §11.5, §14).

The scripted sample call (``advisor_intel.testing.calls``) goes through
``advisor_intel.pipeline.process_call`` — stage 1 on the script, discourse, the orchestrated
extraction with the oracle standing in for the extraction model, the derivation rules, VOI and
follow-ups where installed — and the result is packaged exactly as the review overlay receives it.
The app's contract tests load it beside the hand-built demo package, so the overlay is checked
against what the pipeline really produces: its median document, sidecar (with derivations such as
``gross_from_net``), transcript, flags and follow-ups.

Writes ``tests/fixtures/pipeline_review_package.json``. Run from ``pipeline/``:
``.venv/bin/python scripts/make_pipeline_review_package.py`` (``--check`` only compares).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent
sys.path.insert(0, str(PIPELINE))

from advisor_intel import llm
from advisor_intel.models.forecast import ForecastResult
from advisor_intel.models.review import ReviewBundle, ReviewPackage
from advisor_intel.models.runs import VersionPins
from advisor_intel.pipeline import CallInput, process_call
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript

CALL_ID = "call-scripted-review"
ANCHOR = date(2026, 10, 15)
ORACLE = "oracle-review-package"
SCRIPT = "\n".join(f"{role}: {text}" for role, text in SAMPLE_CALL)
ROLES = {"advisor": "advisor", "client_primary": "client_primary", "client_secondary": "client_secondary"}
OUTPUT = REPO / "tests" / "fixtures" / "pipeline_review_package.json"


async def build() -> ReviewPackage:
    """The sample call through every installed stage, as a review package."""
    oracle = Oracle(await ingested_transcript(SAMPLE_CALL, call_id=CALL_ID, anchor=ANCHOR), SAMPLE_OBJECTS, SAMPLE_GOLD)
    pins = VersionPins(reasoning_model=llm.register_local_model(ORACLE, scripted_model(oracle.handler)))
    try:
        res = await process_call(CallInput(call_id=CALL_ID, anchor_date=ANCHOR, script=SCRIPT, script_roles=ROLES), pins, note=False)
    finally:
        llm.unregister_local_model(ORACLE)
    if res.document is None:
        raise RuntimeError("the pipeline produced no median document")
    # A new client's file gets a fresh id, timestamps and a meeting date of today; fix them to the call date so the fixture is reproducible.
    stamp = f"{ANCHOR.isoformat()}T09:00:00.000Z"
    document = res.document.model_copy(
        update={
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"advisor-intel/{CALL_ID}/document")),
            "createdAt": stamp,
            "updatedAt": stamp,
            "engagement": res.document.engagement.model_copy(update={"meetingDate": ANCHOR.isoformat()}),
        }
    )
    # engagement.meetingDate defaults to today (the app sets it from the call metadata): pin it to the call date too,
    # or the fixture goes stale at midnight.
    document = document.model_copy(update={"engagement": document.engagement.model_copy(update={"meetingDate": ANCHOR.isoformat()})})
    bundle = ReviewBundle(
        call_id=res.call_id,
        run_id=res.run_id,
        profile_version=res.profile.version,
        state="AWAITING_REVIEW",
        median_document=document,
        prior_document=None,
        sidecar=res.sidecar,
        transcript=res.transcript,
        entities=res.profile.entities,
        followups=res.followups,
        blocking_flags=res.flags,
        preview_forecast=res.forecast if isinstance(res.forecast, ForecastResult) else None,
        deterministic_analysis=res.analysis,
        quality=res.transcript.quality,
        versions=pins,
    )
    return ReviewPackage(bundle=bundle)


def render(pkg: ReviewPackage) -> str:
    data = pkg.model_dump(mode="json")
    # Segment and span flags are sets, dumped in hash order (which changes from run to run): sort them.
    transcript = data["bundle"]["transcript"]
    for item in [*transcript["segments"], *transcript["numeric_spans"]]:
        item["flags"] = sorted(item.get("flags", []))
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render(asyncio.run(build()))
    if "--check" in argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print(f"stale: {OUTPUT.relative_to(REPO)} (run scripts/make_pipeline_review_package.py)")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO)} ({len(text.encode()) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
