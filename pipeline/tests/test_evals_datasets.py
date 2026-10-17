"""Dataset sync offline, the §11.4 error and gold-candidate syncs, and the Stage 1–2 cache (§15.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from advisor_intel.evals import datasets as D
from advisor_intel.evals.synthetic import generate_call
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Point, Quantity
from advisor_intel.models.entities import DiscourseOutput
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.profile import FollowUp, Profile
from advisor_intel.models.review import FollowupEdit, ReviewResult
from advisor_intel.models.runs import VersionPins

AT = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
PROV = Provenance(run_id="wf-1")


def ev(quote="I want to retire at sixty"):
    return Evidence(
        segment_id="s0010",
        char_start=0,
        char_end=len(quote),
        quote=quote,
        speaker_role="client_primary",
        t_start=1,
        t_end=2,
        asr_mean_conf=0.9,
        role="supports",
    )


def profile_with_facts():
    p = Profile(call_id="c1", run_id="wf-1", schema_version="profile_v1", anchor_date=date(2026, 10, 15))
    add = lambda slot, inst, path, v, unit="age": (  # noqa: E731
        "ADD",
        f"{slot}#{inst}",
        "s0010",
        {
            "slot_id": slot,
            "instance_key": inst,
            "fna_path": path,
            "hypotheses": [Hypothesis(value=Quantity(dist=Point(value=v), unit=unit), weight=1, rationale="")],
            "evidence": [ev()],
            "provenance": PROV,
            "said": "retire at sixty",
            "interpretation": "retirement age 60",
        },
        "extractor",
    )
    p = apply_state_ops(
        p,
        make_ops(
            p,
            [
                add("retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", 60),
                add("income.{p}.grossMonthly", "client", "income.client.grossMonthly", 45_000, "ZAR"),
            ],
        ),
    ).profile
    return p.model_copy(
        update={
            "open_questions": [
                FollowUp(
                    followup_id="q1",
                    fact_ids=[],
                    question="When do you want to retire?",
                    why="",
                    expected_pos_change=0.1,
                    answer_type="AGE",
                )
            ]
        }
    )


def review_result(profile):
    ops = make_ops(
        profile,
        [
            ("CONFIRM", "income.{p}.grossMonthly#client", None, {"review": {"status": "accepted", "reviewer_id": "adv-1"}}, "reviewer"),
            (
                "RETRACT",
                "retirement.{p}.retirementAge#client",
                None,
                {"reason": "advisor_said_it", "note": "that was my suggestion"},
                "reviewer",
            ),
            (
                "ADD",
                "{p}.smoker#client",
                None,
                {
                    "slot_id": "{p}.smoker",
                    "instance_key": "client",
                    "fna_path": "client.smoker",
                    "source": "reviewer",
                    "hypotheses": [Hypothesis(value=Quantity(dist=Point(value=True), unit="none"), weight=1, rationale="")],
                    "provenance": PROV,
                },
                "reviewer",
            ),
        ],
    )
    return ReviewResult(
        call_id="c1",
        run_id="wf-1",
        base_profile_version=profile.version,
        reviewer_id="adv-1",
        ops=ops,
        submitted=True,
        submitted_at=AT,
        followup_edits=[
            FollowupEdit(followup_id="q1", action="reword", question="Would 60, 63 or 65 be most realistic?", reviewer_id="adv-1", at=AT)
        ],
    )


def test_local_store_upserts_by_id(tmp_path):
    s = D.LocalStore(tmp_path)
    s.insert("x", [D.DatasetRecord(id="a", input=1), D.DatasetRecord(id="b", input=2)])
    s.insert("x", [D.DatasetRecord(id="a", input=3, tags=["t"])])
    rows = {r.id: r for r in s.fetch("x")}
    assert rows["a"].input == 3 and rows["a"].tags == ["t"] and rows["b"].input == 2
    assert s.fetch("missing") == []


def test_braintrust_store_uses_init_dataset():
    calls = []

    class FakeDataset:
        def __init__(self, name):
            self.name, self.rows = name, []

        def insert(self, **kw):
            self.rows.append(kw)
            return kw["id"]

        def flush(self):
            calls.append(("flush", self.name))

        def fetch(self):
            return iter(
                [
                    {"id": r["id"], "input": r["input"], "expected": r["expected"], "metadata": r["metadata"], "tags": r["tags"]}
                    for r in self.rows
                ]
            )

    opened = {}

    def init_dataset(project, name):
        calls.append(("init", project, name))
        return opened.setdefault(name, FakeDataset(name))

    s = D.BraintrustStore("proj", init_dataset=init_dataset)
    s.insert("production-errors", [D.DatasetRecord(id="r1", input={"a": date(2026, 1, 1)}, tags=["slot:x"])])
    assert opened["production-errors"].rows[0] == {
        "input": {"a": "2026-01-01"},
        "expected": None,
        "metadata": {},
        "tags": ["slot:x"],
        "id": "r1",
    }
    assert ("init", "proj", "production-errors") in calls and ("flush", "production-errors") in calls
    assert [r.id for r in s.fetch("production-errors")] == ["r1"]


def test_store_choice_follows_the_key(monkeypatch, tmp_path):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    assert isinstance(D.get_store(root=tmp_path), D.LocalStore)
    monkeypatch.setenv("BRAINTRUST_API_KEY", "placeholder")
    assert isinstance(D.get_store(), D.BraintrustStore)  # nothing is contacted until a dataset is used
    monkeypatch.delenv("BRAINTRUST_API_KEY")
    keyed = D.get_store(settings=SimpleNamespace(braintrust_api_key="from-settings"))
    assert isinstance(keyed, D.BraintrustStore) and keyed._api_key == "from-settings"
    assert isinstance(D.get_store(root=tmp_path, settings=SimpleNamespace(braintrust_api_key=None)), D.LocalStore)


def test_review_events_follow_the_action_table():
    p = profile_with_facts()
    events = D.events_from_review(review_result(p))
    assert [(e.action, e.reason) for e in events] == [("accept", None), ("reject", "advisor_said_it"), ("add", None)]
    assert events[1].note == "that was my suggestion" and events[0].reviewer_id == "adv-1"


def test_sync_error_dataset_tags_rejections_and_missed_slots(tmp_path):
    p = profile_with_facts()
    store = D.LocalStore(tmp_path)
    events = D.events_from_review(review_result(p))
    assert D.sync_error_dataset(events, profiles={"c1": p}, pins=VersionPins(), store=store) == 2
    rows = {r.metadata["kind"]: r for r in store.fetch("production-errors")}
    rej, miss = rows["rejected"], rows["missed"]
    assert set(rej.tags) == {"slot:retirement.{p}.retirementAge", "reason:advisor_said_it", "group:retirement", "tier:A", "rejected"}
    assert rej.input["quotes"] == ["I want to retire at sixty"] and rej.input["extracted"] == "60"
    assert miss.metadata["reason"] == "missed_slot" and "slot:{p}.smoker" in miss.tags
    # idempotent: the nightly run can repeat
    ids = {r.id for r in store.fetch("production-errors")}
    assert D.sync_error_dataset(events, profiles={"c1": p}, store=store) == 2
    assert {r.id for r in store.fetch("production-errors")} == ids
    assert D.sync_error_dataset([events[0]], store=store) == 0  # an accept is not an error


def test_sync_error_dataset_takes_the_nightly_activity_rows(tmp_path):
    """The nightly activity hands over rows built from the ``review_events`` table, with its settings."""
    store = D.LocalStore(tmp_path)
    value = Quantity(dist=Point(value=60), unit="age").model_dump(mode="json")
    rows = [
        {
            "run_id": "wf-9",
            "fact_id": "retirement.{p}.retirementAge#client",
            "slot_id": "retirement.{p}.retirementAge",
            "action": "reject",
            "reason": "misheard",
            "quotes": ["retire at sixty", None],
            "value": value,
            "interpretation": "retirement age 60",
            "note": "fifteen, not fifty",
            "reviewer_id": "adv-2",
            "at": AT.isoformat(),
            "tags": ["retirement", "misheard"],
        },
        {
            "run_id": "wf-9",
            "fact_id": "{p}.smoker#client",
            "slot_id": "{p}.smoker",
            "action": "add",
            "reason": "missed_slot",
            "quotes": [],
            "value": None,
            "interpretation": None,
            "note": None,
            "reviewer_id": "adv-2",
            "at": AT.isoformat(),
            "tags": ["client", "missed_slot"],
        },
    ]
    assert D.sync_error_dataset(rows, settings=SimpleNamespace(braintrust_api_key=None), store=store) == 2
    got = {r.metadata["kind"]: r for r in store.fetch("production-errors")}
    rej = got["rejected"]
    assert rej.input["quotes"] == ["retire at sixty"] and rej.input["extracted"] == "60" and rej.input["run_id"] == "wf-9"
    assert rej.expected["note"] == "fifteen, not fifty" and "reason:misheard" in rej.tags and rej.metadata["tier"] == "A"
    assert got["missed"].metadata["reason"] == "missed_slot"


def test_sync_gold_candidates(tmp_path):
    p = profile_with_facts()
    store = D.LocalStore(tmp_path)
    r = review_result(p)
    reviewed = apply_state_ops(p, r.ops).profile
    assert D.sync_gold_candidates([r], profiles={"c1": reviewed}, store=store) == 2
    cand = store.fetch("gold-candidates")[0]
    assert cand.id == "c1:wf-1" and cand.metadata["corrections"] == {"reject": 1, "add": 1}
    assert cand.expected["facts"]["retirement.{p}.retirementAge#client"]["review"] == "rejected"
    fu = store.fetch("followup-edits")[0]
    assert fu.input["original"] == "When do you want to retire?" and fu.expected["action"] == "reword"


def test_sync_gold_candidates_takes_the_nightly_activity_rows(tmp_path):
    store = D.LocalStore(tmp_path)
    edit = FollowupEdit(followup_id="q1", action="drop", reviewer_id="adv-1", at=AT).model_dump(mode="json")
    rows = [
        {
            "run_id": "wf-3",
            "call_id": "c3",
            "versions": VersionPins().model_dump(mode="json"),
            "reviewed_profile": "profiles/wf-3/",
            "corrections": [{"action": "reject", "fact_id": "x#client", "reviewer_id": "adv-1"}, {"action": "add", "fact_id": "y#client"}],
            "followup_edits": [edit],
        },
        {"run_id": "wf-4", "call_id": "c4", "versions": {}, "reviewed_profile": "profiles/wf-4/", "corrections": [], "followup_edits": []},
    ]
    assert D.sync_gold_candidates(rows, settings=None, store=store) == 2
    cand = store.fetch("gold-candidates")[0]
    assert (
        cand.id == "c3:wf-3"
        and cand.expected["reviewed_profile"] == "profiles/wf-3/"
        and cand.metadata["corrections"] == {"reject": 1, "add": 1}
    )
    assert store.fetch("followup-edits")[0].expected["action"] == "drop"


def test_stage12_key_ignores_extraction_changes():
    base = VersionPins(prompt_versions={"extract_slot_group/income": "v1", "discourse/rewrite": "v1"})
    same = base.model_copy(
        update={
            "reasoning_model": "anthropic:other",
            "verifier_model": "openai:x",
            "calibrator_version": "v9",
            "prompt_versions": {"extract_slot_group/income": "v2", "discourse/rewrite": "v1"},
        }
    )
    assert D.stage12_key(base) == D.stage12_key(same)
    assert D.stage12_key(base) != D.stage12_key(base.model_copy(update={"asr_model_version": "nova-3"}))
    assert D.stage12_key(base) != D.stage12_key(base.model_copy(update={"prompt_versions": {"discourse/rewrite": "v2"}}))
    # the pipeline's own stage 2 prompt names ("discourse_entities", …) count too; extraction and verifier prompts do not
    flat = VersionPins(prompt_versions={"discourse_entities": "v1", "extract_slot_group/income": "v1", "entailment": "v1"})
    assert D.stage12_key(flat) != D.stage12_key(
        flat.model_copy(update={"prompt_versions": {**flat.prompt_versions, "discourse_entities": "v2"}})
    )
    assert D.stage12_key(flat) == D.stage12_key(flat.model_copy(update={"prompt_versions": {**flat.prompt_versions, "entailment": "v3"}}))
    # human transcripts do not depend on the ASR pins
    assert D.stage12_key(base, "human") == D.stage12_key(base.model_copy(update={"asr_model_version": "nova-3"}), "human")


def test_stage12_cache_builds_once_then_hits(tmp_path):
    call = generate_call(0, seed=2)
    rec = D.synthetic_record(call)
    rec_no_script = rec.model_copy(update={"input": {k: v for k, v in rec.input.items() if k != "script"}})
    cache = D.Stage12Cache(D.LocalBlobStore(tmp_path))
    built = []

    def build(record, pins):
        built.append(record.id)
        return call.transcript(), DiscourseOutput(entities={})

    pins = VersionPins()
    a = cache.load_call(rec_no_script, pins, build=build)
    b = cache.load_call(rec_no_script, pins.model_copy(update={"reasoning_model": "anthropic:x"}), build=build)
    assert built == [rec.id] and a.transcript == b.transcript and b.discourse is not None
    assert a.prior_document == call.prior_document and a.anchor_date == call.anchor_date and a.call_minutes > 0
    with pytest.raises(KeyError):
        D.Stage12Cache(D.LocalBlobStore(tmp_path / "empty")).load_call(rec_no_script, pins)
    # a synthetic record carries its script, so Stage 1 can be rebuilt from it offline
    c = D.Stage12Cache(D.LocalBlobStore(tmp_path / "other")).load_call(rec, pins)
    assert c.transcript == call.transcript() and " ".join(s.text for s in c.transcript.segments) == " ".join(t.text for t in call.script)


def test_s3_blob_store_with_a_fake_client():
    class NoSuchKey(Exception):
        pass

    class Body:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

    class Client:
        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 argument names
            self.objects[(Bucket, Key)] = Body

        def get_object(self, Bucket, Key):  # noqa: N803
            if (Bucket, Key) not in self.objects:
                raise NoSuchKey(Key)
            return {"Body": Body(self.objects[(Bucket, Key)])}

    client = Client()
    s = D.S3BlobStore("bucket", "evals/", client=client)
    assert s.read("a/b.json") is None
    s.write("a/b.json", b"{}")
    assert client.objects[("bucket", "evals/a/b.json")] == b"{}" and s.read("a/b.json") == b"{}"


def test_gold_records_one_per_transcript_kind(tmp_path):
    call = generate_call(1, seed=2)
    gold = call.gold.model_copy(update={"source": "real", "tags": ["gold"], "call_id": "real-1"})
    store = D.LocalStore(tmp_path)
    D.sync_gold([gold], store=store)
    rows = store.fetch("gold-calls")
    assert sorted(r.id for r in rows) == ["real-1:asr", "real-1:human"]
    assert all(r.metadata["dataset"] == "gold-calls" and "split:dev" in r.tags for r in rows)
    D.sync_transcripts([call.transcript()], store=store)
    assert store.fetch("gold-transcripts")[0].metadata["segments"] == len(call.transcript().segments)


def test_the_stage12_builder_runs_stage_one_and_two(tmp_path):
    """On a cache miss: the transcript from the script, the row or gold-transcripts, then the real discourse stage."""
    pytest.importorskip("advisor_intel.discourse.run")
    call = generate_call(3, seed=2)
    store = D.LocalStore(tmp_path)
    build = D.stage12_builder(store=store)
    t, d = build(D.synthetic_record(call), VersionPins())
    assert t == call.transcript() and d is not None and d.entities  # the household's people are in the registry
    gold = call.gold.model_copy(update={"source": "real", "call_id": "real-3"})
    D.sync_gold([gold], store=store, transcript_kinds=("asr",))
    rec = store.fetch("gold-calls")[0]
    with pytest.raises(KeyError, match="sync_transcripts"):
        D.stage12_builder(store=store, discourse=False)(rec, VersionPins())
    asr = call.transcript().model_copy(update={"call_id": "real-3"})
    D.sync_transcripts([asr], store=store, kind="asr")
    t2, d2 = D.stage12_builder(store=store, discourse=False)(rec, VersionPins())
    assert t2 == asr and d2 is None
    cached = D.Stage12Cache(D.LocalBlobStore(tmp_path / "cache")).load_call(rec, VersionPins(), build=D.stage12_builder(store=store))
    assert cached.transcript == asr and cached.discourse is not None


def test_the_dataset_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    assert D.main(["sync-synthetic", "--n", "2", "--seed", "4"]) == 0
    assert "2 synthetic calls synced" in capsys.readouterr().out
    assert len(D.LocalStore().fetch("synthetic-calls")) == 2
    t = generate_call(0, seed=4).transcript()
    (tmp_path / "t.json").write_text(t.model_dump_json())
    assert D.main(["sync-transcripts", str(tmp_path / "t.json"), "--kind", "asr"]) == 0
    assert D.LocalStore().fetch("gold-transcripts")[0].id == f"{t.call_id}:asr"


def test_the_braintrust_eval_file_registers_the_experiment(tmp_path):
    """``braintrust eval --list`` imports eval_extraction.py the way CI does and finds the Eval."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    pipeline_dir = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k != "BRAINTRUST_API_KEY"}
    env.update(ADVISOR_INTEL_DATA_DIR=str(tmp_path), PYDANTIC_AI_NO_BANNER="1", PYTHONPATH=str(pipeline_dir))
    cli = Path(sys.executable).with_name("braintrust")
    proc = subprocess.run(
        [str(cli), "eval", "--list", "--no-send-logs", "advisor_intel/evals/eval_extraction.py"],
        cwd=pipeline_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "advisor-extraction" in proc.stdout


def test_export_snapshots_a_dataset_to_object_storage(tmp_path):
    """§15.1: the gold calls are exported nightly to object storage, dated and as ``latest``."""
    store = D.LocalStore(tmp_path / "ds")
    call = generate_call(1, seed=2)
    D.sync_gold([call.gold.model_copy(update={"source": "real", "call_id": "real-1"})], store=store)
    blobs = D.LocalBlobStore(tmp_path / "blobs")
    key = D.export_dataset("gold-calls", store=store, blobs=blobs, at=datetime(2026, 10, 16, 2, 30, tzinfo=timezone.utc))
    assert key == "datasets/gold-calls/2026-10-16.jsonl"
    rows = [D.DatasetRecord.model_validate_json(line) for line in blobs.read(key).decode().splitlines()]
    assert [r.id for r in rows] == ["real-1:asr", "real-1:human"] and rows[0].expected["call_id"] == "real-1"
    assert blobs.read("datasets/gold-calls/latest.jsonl") == blobs.read(key)
    assert D.export_dataset("gold-transcripts", store=store, blobs=blobs).startswith("datasets/gold-transcripts/")
    assert blobs.read("datasets/gold-transcripts/latest.jsonl") == b""  # nothing synced yet: an empty snapshot, not an error


def test_the_export_cli_writes_the_default_datasets(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADVISOR_INTEL_OBJECT_STORE_URI", f"file://{tmp_path / 'objects'}")
    assert D.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "gold-calls exported to datasets/gold-calls/" in out and "gold-transcripts exported to" in out
    assert (tmp_path / "cache" / "datasets" / "gold-calls" / "latest.jsonl").exists()  # the local blob store under the data dir
