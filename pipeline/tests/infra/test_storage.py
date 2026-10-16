"""Postgres storage on a throwaway local cluster: migrations, append-only facts, profiles, idempotency,
checkpoints and the glossary (spec §14.2)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models.forecast import ForecastResult
from advisor_intel.models.review import FollowupEdit
from advisor_intel.models.runs import FollowupAnswer, Stage, VersionPins
from advisor_intel.storage import (
    CalibratorRecord,
    CallRecord,
    GlossaryEntry,
    PostgresCheckpointStore,
    PostgresGlossaryStore,
    ReviewEvent,
    StaleProfile,
    Store,
    SyncGlossaryStore,
)
from advisor_intel.storage.migrate import SCHEMA_FILE, MigrationError, discover, migrate, render_schema, status

from .factories import GROSS, RET_AGE, flagged, make_transcript, reviewer_accept, sample_document, seeded_profile, with_call_facts

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


async def _setup_call(store: Store, call_id: str = "call-1", run_id: str = "call-1-run-a") -> None:
    await store.upsert_call(CallRecord(call_id=call_id, advisor_id="adv-1", client_ids=["cl-1"], started_at=NOW))
    await store.register_run(run_id, call_id, VersionPins())


# --------------------------------------------------------------------------- migrations


async def test_migrations_apply_once_and_record_checksums(empty_pg_dsn):
    applied = await migrate(empty_pg_dsn)
    assert applied == [m.version for m in discover()]
    assert await migrate(empty_pg_dsn) == []
    st = await status(empty_pg_dsn)
    assert all(s.applied_at is not None for s in st)


async def test_changed_migration_is_refused(empty_pg_dsn, tmp_path: Path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "0001_first.sql").write_text("CREATE TABLE a (x int);")
    assert await migrate(empty_pg_dsn, directory=d) == [1]
    (d / "0001_first.sql").write_text("CREATE TABLE a (x bigint);")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        await migrate(empty_pg_dsn, directory=d)
    (d / "0001_first.sql").write_text("CREATE TABLE a (x int);")
    (d / "0003_gap.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="without gaps"):
        await migrate(empty_pg_dsn, directory=d)


async def test_schema_file_is_the_rendered_migrations_and_applies(empty_pg_dsn, pg_dsn):
    assert SCHEMA_FILE.read_text() == render_schema(), "storage/schema.sql is stale: run `python -m advisor_intel.storage.migrate render`"
    async with await psycopg.AsyncConnection.connect(empty_pg_dsn, autocommit=True) as conn:
        await conn.execute(SCHEMA_FILE.read_text())
        a = await (
            await conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1")
        ).fetchall()
    async with await psycopg.AsyncConnection.connect(pg_dsn, autocommit=True) as conn:
        b = await (
            await conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name <> 'schema_migrations' ORDER BY 1"
            )
        ).fetchall()
    assert a == b
    names = {r[0] for r in a}
    assert {"calls", "run_versions", "transcripts", "entities", "facts", "facts_latest", "state_ops", "agent_checkpoints", "followups",
            "review_events", "forecasts", "calibrators", "lexicons", "glossary"} <= names  # fmt: skip


# --------------------------------------------------------------------------- profiles and append-only facts


async def test_profile_round_trip(store: Store):
    await _setup_call(store)
    p = flagged(with_call_facts(seeded_profile()))
    saved = await store.save_profile(p)
    loaded = await store.load_profile(p.run_id)
    assert loaded is not None
    assert loaded.version == saved.version
    assert loaded.facts == p.facts
    assert [o.seq for o in loaded.ops_log] == [o.seq for o in p.ops_log]
    assert list(loaded.entities) == list(p.entities)  # registry order is kept: list rows materialise in it
    assert loaded.blocking_flags == p.blocking_flags and loaded.open_questions == p.open_questions
    assert check_profile(loaded) == []


async def test_facts_are_append_only_with_a_latest_view(store: Store, pg_dsn):
    await _setup_call(store)
    p = with_call_facts(seeded_profile())
    p = await store.save_profile(p)
    # A reviewer accepts the retirement age: one new version of that fact only.
    p2 = apply_state_ops(p, reviewer_accept(p, RET_AGE)).profile
    await store.save_profile(p2)
    hist = await store.fact_history(p.run_id, RET_AGE)
    assert [h.version for h in hist] == [1, 2]
    assert hist[0].fact["review"]["status"] == "pending" and hist[1].fact["review"]["status"] == "accepted"
    assert len(await store.fact_history(p.run_id, GROSS)) == 1
    latest = await store.latest_facts(p.run_id, [RET_AGE])
    assert latest[RET_AGE].review.status == "accepted"
    # A verifier removes the income fact: a tombstone, gone from the view, still in the history.
    p3 = apply_state_ops(p2, make_ops(p2, [("RETRACT", GROSS, None, {"reason": "extraction_error"}, "verifier")])).profile
    await store.save_profile(p3)
    assert GROSS not in await store.latest_facts(p.run_id)
    assert [h.deleted for h in await store.fact_history(p.run_id, GROSS)] == [False, True]
    async with await psycopg.AsyncConnection.connect(pg_dsn, autocommit=True) as conn:
        for sql in (
            "UPDATE facts SET profile_version = 99",
            "DELETE FROM facts",
            "DELETE FROM state_ops",
            "UPDATE review_events SET reason = 'x'",
            "TRUNCATE facts",
        ):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                await conn.execute(sql)


async def test_profile_versions_freeze_and_old_versions_load(store: Store):
    await _setup_call(store)
    p = await store.save_profile(with_call_facts(seeded_profile()))
    v = p.version
    await store.freeze_profile(p.run_id, v, frozen_by="adv-1")
    # An annotation-only save at the frozen version becomes a new version; the frozen one is unchanged.
    annotated = p.model_copy(update={"facts": {**p.facts, RET_AGE: p.facts[RET_AGE].model_copy(update={"p_correct": 0.97})}})
    saved = await store.save_profile(annotated)
    assert saved.version == v + 1
    old = await store.load_profile(p.run_id, v)
    new = await store.load_profile(p.run_id)
    assert old.facts[RET_AGE].p_correct == 0.5 and new.facts[RET_AGE].p_correct == 0.97
    versions = await store.profile_versions(p.run_id)
    assert [(r.version, r.frozen) for r in versions] == [(v, True), (v + 1, False)]
    with pytest.raises(StaleProfile):
        await store.save_profile(p.model_copy(update={"version": v - 1}))


async def test_update_profile_serialises_concurrent_writers(store: Store):
    await _setup_call(store)
    p = await store.save_profile(with_call_facts(seeded_profile()))

    def accept(fid):
        def fn(prof):
            res = apply_state_ops(prof, reviewer_accept(prof, fid))
            return res.profile, len(res.applied)

        return fn

    results = await asyncio.gather(store.update_profile(p.run_id, accept(RET_AGE)), store.update_profile(p.run_id, accept(GROSS)))
    assert [n for _, n in results] == [1, 1]
    final = await store.load_profile(p.run_id)
    assert final.facts[RET_AGE].review.status == "accepted" and final.facts[GROSS].review.status == "accepted"
    seqs = [o.seq for o in final.ops_log]
    assert seqs == list(range(1, len(seqs) + 1))
    assert final.version == p.version + 2
    assert check_profile(final) == []


# --------------------------------------------------------------------------- idempotency and caches


async def test_stage_outputs_first_writer_wins(store: Store):
    await _setup_call(store)
    a = await store.put_stage_output("call-1", "ingest", "k1", "call-1-run-a", {"n": 1})
    b = await store.put_stage_output("call-1", "ingest", "k1", "call-1-run-b", {"n": 2})
    assert a.output == b.output == {"n": 1} and b.run_id == "call-1-run-a"
    assert await store.get_stage_output("call-1", "ingest", "k2") is None


async def test_transcripts_are_cached_by_asr_key(store: Store):
    await _setup_call(store)
    t = make_transcript()
    await store.save_transcript("call-1", "asr-key-1", t, uri="file:///x/t.json")
    assert (await store.load_transcript("call-1", "asr-key-1")) == t
    assert (await store.load_transcript("call-1")) == t
    assert await store.load_transcript("call-1", "other") is None
    q = await store.transcript_quality("call-1", "asr-key-1")
    assert q is not None and q.n_speakers == 2


async def test_runs_register_once(store: Store):
    await store.upsert_call(CallRecord(call_id="call-1", advisor_id="adv-1", client_ids=["cl-1"]))
    r1 = await store.register_run("run-a", "call-1", VersionPins(), start_stage=Stage.INGEST)
    r2 = await store.register_run("run-a", "call-1", VersionPins(lexicon_version="9"), start_stage=Stage.FORECAST)
    assert r1 == r2 and r2.versions.lexicon_version == VersionPins().lexicon_version
    await store.register_run("run-b", "call-1", VersionPins(cma_version="2027Q1"), start_stage=Stage.FORECAST, source_run_id="run-a")
    assert [r.workflow_id for r in await store.runs_for_call("call-1")] == ["run-b", "run-a"]
    assert (await store.latest_run("call-1")).source_run_id == "run-a"


# --------------------------------------------------------------------------- checkpoints


async def test_checkpoints_resume_latest_turn_per_execution(store: Store):
    cp = PostgresCheckpointStore(store, scope="exec-1")
    assert await cp.load("run-a") is None
    await cp.save("run-a", 1, [{"kind": "request", "parts": []}], {"plan": {"tasks": 3}})
    await cp.save("run-a", 2, [{"kind": "request"}, {"kind": "response"}], {"plan": {"tasks": 2}})
    await cp.save("run-a", 1, [], {})  # a late write from an older attempt never overwrites a newer turn
    got = await cp.load("run-a")
    assert got.turn == 2 and len(got.messages) == 2 and got.harness_state == {"plan": {"tasks": 2}}
    assert await PostgresCheckpointStore(store, scope="exec-2").load("run-a") is None  # a reset starts clean


# --------------------------------------------------------------------------- glossary


async def test_glossary_get_put_search(store: Store, pg_dsn):
    g = PostgresGlossaryStore(store.db)
    await g.put(
        GlossaryEntry(
            term="10X RA",
            canonical_name="10X Retirement Annuity",
            aliases=["ten x ra"],
            product_type="retirement-annuity",
            provider="10X",
            confirmed_by="adv-1",
        )
    )
    await g.put(
        GlossaryEntry(term="Discovery Classic", canonical_name="Discovery Classic Saver", product_type="medical", provider="Discovery")
    )
    hit = await g.get("10x  ra")
    assert hit is not None and hit.canonical_name == "10X Retirement Annuity"
    assert (await g.get("Ten X RA")).term == "10X RA"
    await g.put(
        GlossaryEntry(
            term="10X RA", canonical_name="10X Retirement Annuity", aliases=["tenex"], product_type="retirement-annuity", provider="10X"
        )
    )
    assert set((await g.get("10x ra")).aliases) == {"ten x ra", "tenex"}
    found = await g.search("discovery saver")
    assert found and found[0].provider == "Discovery"
    assert (await g.search("10x"))[0].term == "10X RA"
    assert await g.get("unknown term") is None
    sync = SyncGlossaryStore(pg_dsn)
    assert (await asyncio.to_thread(sync.get, "tenex")).canonical_name == "10X Retirement Annuity"


# --------------------------------------------------------------------------- follow-ups, review, forecasts, documents, notifications


async def test_followups_status_never_moves_back(store: Store):
    await _setup_call(store)
    p = await store.save_profile(flagged(with_call_facts(seeded_profile())))
    ans = FollowupAnswer(call_id="call-1", followup_id="fu.retirement_age", text="sixty-three", answered_at=NOW)
    rec = await store.record_followup_answer(p.run_id, ans)
    assert rec.status == "answered" and rec.answer.text == "sixty-three"
    await store.save_profile(p)  # the agent's copy still says "open"
    assert (await store.get_followup(p.run_id, "fu.retirement_age")).status == "answered"
    added = await store.apply_followup_edit(
        p.run_id, FollowupEdit(followup_id=None, action="add", question="Any offshore assets?", reviewer_id="adv-1", at=NOW)
    )
    await store.apply_followup_edit(
        p.run_id,
        FollowupEdit(followup_id="fu.retirement_age", action="reword", question="Retire at 60 or 65?", reviewer_id="adv-1", at=NOW),
    )
    qs = {r.followup.followup_id: r for r in await store.list_followups(p.run_id)}
    assert qs["fu.retirement_age"].followup.question == "Retire at 60 or 65?" and qs[added].followup.question == "Any offshore assets?"
    loaded = await store.load_profile(p.run_id)
    assert {q.followup_id: q.status for q in loaded.open_questions} == {"fu.retirement_age": "answered", added: "open"}


async def test_review_events_forecasts_documents_notifications(store: Store):
    await _setup_call(store)
    ev = ReviewEvent(workflow_id="call-1-run-a", fact_id=RET_AGE, action="accept", reviewer_id="adv-1", at=NOW, detail={"note": "ok"})
    await store.record_review_events(
        [ev, ev.model_copy(update={"action": "reject", "reason": "misheard", "at": NOW + timedelta(hours=30)})]
    )
    assert [e.action for e in await store.review_events(since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))] == ["accept"]
    assert len(await store.review_events(run_id="call-1-run-a")) == 2

    fr = ForecastResult(
        run_id="call-1-run-a",
        profile_version="3",
        config_version="config_v1",
        cma_version="2026Q4",
        n_paths=1000,
        pos=0.71,
        pos_ci=(0.68, 0.74),
    )
    await store.save_forecast("call-1-run-a", 3, "preview", fr, config_version="config_v1", cma_version="2026Q4")
    await store.save_forecast(
        "call-1-run-a", 4, "final", fr.model_copy(update={"pos": 0.75}), config_version="config_v1", cma_version="2026Q4"
    )
    assert (await store.get_forecast("call-1-run-a")).result["pos"] == 0.75
    assert (await store.get_forecast("call-1-run-a", mode="preview")).profile_version == 3

    doc = sample_document()
    first = await store.save_client_document("cl-1", doc, source="import")
    doc2 = doc.model_copy(update={"notes": "after review"})
    await store.save_client_document("cl-1", doc2, source="reviewed", call_id="call-1")
    assert (await store.latest_client_document("cl-1")).notes == "after review"
    assert (await store.latest_client_document("cl-1", before_id=first + 1)).notes == doc.notes

    n1 = await store.claim_notification("run-a/review_ready", run_id="call-1-run-a", kind="review_ready", recipient="a@x.co", payload={})
    assert n1.sent_at is None
    await store.mark_notification("run-a/review_ready", channel="log")
    n2 = await store.claim_notification("run-a/review_ready", run_id="call-1-run-a", kind="review_ready", recipient="a@x.co", payload={})
    assert n2.sent_at is not None and n2.channel == "log"

    await store.register_calibrator(
        CalibratorRecord(version="cal-2026-10-16", slot_group="*", artifact_uri="file:///c.pkl", metrics={"ece": 0.04})
    )
    await store.register_calibrator(
        CalibratorRecord(version="cal-2026-10-16", slot_group="*", tau=0.93, alpha=0.1, n_calib_calls=60, promoted=True)
    )
    [cal] = await store.calibrators(version="cal-2026-10-16")
    assert cal.artifact_uri == "file:///c.pkl" and cal.tau == 0.93 and cal.promoted and cal.metrics == {"ece": 0.04}
    assert await store.current_calibrator_version() == "cal-2026-10-16"

    await store.save_lexicon("1.4", "entries: []\n", approved_by="adv-1")
    await store.save_lexicon("1.4", "entries: []\n")
    with pytest.raises(ValueError, match="different content"):
        await store.save_lexicon("1.4", "entries: [x]\n")
