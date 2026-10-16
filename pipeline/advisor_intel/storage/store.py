"""The async repository over Postgres that activities and the API use (spec §14.2).

Profiles are stored relationally, not as blobs: a header row per saved version (``profiles``),
append-only fact versions (``facts``; a removed fact is a tombstone), the replay log
(``state_ops``), the entity registry, blocking flags and follow-ups. ``save_profile`` writes only
what changed; ``load_profile(run_id, version)`` reassembles any saved version. Writers that
read-modify-write a profile (reviewer ops, follow-up answers) go through ``update_profile``,
which holds a per-run advisory lock for the whole transaction, so concurrent edits never lose
an update. A frozen version (review submitted, §11.2) is never rewritten: a later save becomes
a new version.

Stage outputs are cached per ``(call_id, stage, cache_key)`` (``stage_outputs``), which is how
activities stay idempotent and how re-runs reuse earlier outputs (§2.1, §13.1).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Any, TypeVar

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..models.crm import CrmRecord
from ..models.entities import DiscourseOutput, Entity
from ..models.facts import Fact
from ..models.fna import FnaDocument
from ..models.forecast import ForecastResult
from ..models.profile import BlockingFlag, FollowUp, Profile, StateOp
from ..models.review import FollowupEdit, ReviewResult
from ..models.runs import FollowupAnswer, Stage, VersionPins
from ..models.transcript import QualityReport, Transcript
from .db import Connection, Database
from .records import (
    STATUS_RANK,
    CalibratorRecord,
    CallRecord,
    FactVersion,
    FollowupRecord,
    ForecastRecord,
    NotificationRecord,
    ProfileVersionRecord,
    ReviewEvent,
    ReviewResultRecord,
    RunRecord,
    StageRecord,
)

T = TypeVar("T")


class StaleProfile(RuntimeError):
    """A save built on an older profile version than the one stored."""


class NotFound(LookupError):
    pass


def _json(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    return obj


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(_json(obj), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class Store:
    """Every read and write the pipeline makes to Postgres."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @classmethod
    def from_dsn(cls, dsn: str, *, max_size: int = 10) -> "Store":
        return cls(Database(dsn, max_size=max_size))

    async def close(self) -> None:
        await self.db.close()

    async def ping(self) -> bool:
        return await self.db.ping()

    @staticmethod
    async def _lock_run(conn: Connection, run_id: str) -> None:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 7))", (f"run:{run_id}",))

    # ------------------------------------------------------------------ calls and runs

    async def upsert_call(self, call: CallRecord) -> CallRecord:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO calls (call_id, workflow_id, advisor_id, client_ids, started_at, audio_uri, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_id) DO UPDATE SET
                  workflow_id = COALESCE(EXCLUDED.workflow_id, calls.workflow_id),
                  advisor_id = EXCLUDED.advisor_id,
                  client_ids = EXCLUDED.client_ids,
                  started_at = COALESCE(EXCLUDED.started_at, calls.started_at),
                  audio_uri = COALESCE(EXCLUDED.audio_uri, calls.audio_uri),
                  meta = calls.meta || EXCLUDED.meta
                RETURNING *
                """,
                (call.call_id, call.workflow_id, call.advisor_id, call.client_ids, call.started_at, call.audio_uri, Jsonb(call.meta)),
            )
            return CallRecord.model_validate(await cur.fetchone())

    async def get_call(self, call_id: str) -> CallRecord | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM calls WHERE call_id = %s", (call_id,))
            row = await cur.fetchone()
        return CallRecord.model_validate(row) if row else None

    async def set_call_workflow(self, call_id: str, workflow_id: str) -> None:
        async with self.db.connection() as conn:
            await conn.execute("UPDATE calls SET workflow_id = %s WHERE call_id = %s", (workflow_id, call_id))

    async def register_run(
        self, run_id: str, call_id: str, versions: VersionPins, *, start_stage: int = Stage.INGEST, source_run_id: str | None = None
    ) -> RunRecord:
        """Record a run's pinned versions (idempotent: the first registration wins)."""
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO run_versions (workflow_id, call_id, versions, versions_key, start_stage, source_run_id)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (workflow_id) DO NOTHING
                """,
                (run_id, call_id, Jsonb(versions.model_dump(mode="json")), versions.key(), int(start_stage), source_run_id),
            )
            cur = await conn.execute("SELECT * FROM run_versions WHERE workflow_id = %s", (run_id,))
            return RunRecord.model_validate(await cur.fetchone())

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM run_versions WHERE workflow_id = %s", (run_id,))
            row = await cur.fetchone()
        return RunRecord.model_validate(row) if row else None

    async def runs_for_call(self, call_id: str) -> list[RunRecord]:
        """The call's runs, newest first."""
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM run_versions WHERE call_id = %s ORDER BY created_at DESC, workflow_id DESC", (call_id,))
            return [RunRecord.model_validate(r) for r in await cur.fetchall()]

    async def latest_run(self, call_id: str) -> RunRecord | None:
        runs = await self.runs_for_call(call_id)
        return runs[0] if runs else None

    # ------------------------------------------------------------------ stage outputs (idempotency)

    async def get_stage_output(self, call_id: str, stage: str, cache_key: str) -> StageRecord | None:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM stage_outputs WHERE call_id = %s AND stage = %s AND cache_key = %s", (call_id, stage, cache_key)
            )
            row = await cur.fetchone()
        return StageRecord.model_validate(row) if row else None

    async def put_stage_output(
        self, call_id: str, stage: str, cache_key: str, run_id: str, output: dict[str, Any], versions: dict[str, Any] | None = None
    ) -> StageRecord:
        """Record a finished stage. If another attempt already recorded it, that record wins and is returned."""
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO stage_outputs (call_id, stage, cache_key, run_id, versions, output)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (call_id, stage, cache_key) DO NOTHING
                """,
                (call_id, stage, cache_key, run_id, Jsonb(versions or {}), Jsonb(output)),
            )
            cur = await conn.execute(
                "SELECT * FROM stage_outputs WHERE call_id = %s AND stage = %s AND cache_key = %s", (call_id, stage, cache_key)
            )
            return StageRecord.model_validate(await cur.fetchone())

    async def stage_outputs_for_run(self, run_id: str) -> list[StageRecord]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM stage_outputs WHERE run_id = %s ORDER BY created_at", (run_id,))
            return [StageRecord.model_validate(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------ transcripts and discourse

    async def save_transcript(self, call_id: str, cache_key: str, transcript: Transcript, *, uri: str | None = None) -> None:
        quality = transcript.quality.model_dump(mode="json") if transcript.quality else None
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO transcripts (call_id, asr_model_version, cache_key, transcript, quality, transcript_uri)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_id, cache_key) DO UPDATE SET transcript = EXCLUDED.transcript, quality = EXCLUDED.quality,
                  transcript_uri = COALESCE(EXCLUDED.transcript_uri, transcripts.transcript_uri)
                """,
                (call_id, transcript.asr_model_version, cache_key, Jsonb(transcript.model_dump(mode="json")), Jsonb(quality) if quality else None, uri),
            )

    async def load_transcript(self, call_id: str, cache_key: str | None = None) -> Transcript | None:
        """The transcript for one ASR cache key, or the latest one for the call."""
        async with self.db.connection() as conn:
            if cache_key is None:
                cur = await conn.execute("SELECT transcript FROM transcripts WHERE call_id = %s ORDER BY created_at DESC LIMIT 1", (call_id,))
            else:
                cur = await conn.execute("SELECT transcript FROM transcripts WHERE call_id = %s AND cache_key = %s", (call_id, cache_key))
            row = await cur.fetchone()
        return Transcript.model_validate(row["transcript"]) if row else None

    async def transcript_quality(self, call_id: str, cache_key: str | None = None) -> QualityReport | None:
        async with self.db.connection() as conn:
            if cache_key is None:
                cur = await conn.execute("SELECT quality FROM transcripts WHERE call_id = %s ORDER BY created_at DESC LIMIT 1", (call_id,))
            else:
                cur = await conn.execute("SELECT quality FROM transcripts WHERE call_id = %s AND cache_key = %s", (call_id, cache_key))
            row = await cur.fetchone()
        return QualityReport.model_validate(row["quality"]) if row and row["quality"] else None

    async def save_discourse(self, call_id: str, cache_key: str, output: DiscourseOutput, *, versions: dict[str, Any] | None = None, uri: str | None = None) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO discourse_outputs (call_id, cache_key, versions, output, output_uri) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (call_id, cache_key) DO UPDATE SET output = EXCLUDED.output, versions = EXCLUDED.versions,
                  output_uri = COALESCE(EXCLUDED.output_uri, discourse_outputs.output_uri)
                """,
                (call_id, cache_key, Jsonb(versions or {}), Jsonb(output.model_dump(mode="json")), uri),
            )

    async def load_discourse(self, call_id: str, cache_key: str | None = None) -> DiscourseOutput | None:
        async with self.db.connection() as conn:
            if cache_key is None:
                cur = await conn.execute("SELECT output FROM discourse_outputs WHERE call_id = %s ORDER BY created_at DESC LIMIT 1", (call_id,))
            else:
                cur = await conn.execute("SELECT output FROM discourse_outputs WHERE call_id = %s AND cache_key = %s", (call_id, cache_key))
            row = await cur.fetchone()
        return DiscourseOutput.model_validate(row["output"]) if row else None

    # ------------------------------------------------------------------ profiles

    async def save_profile(self, profile: Profile) -> Profile:
        """Persist what changed since the last save; returns the profile as saved (its version may be bumped)."""
        async with self.db.transaction() as conn:
            await self._lock_run(conn, profile.run_id)
            return await self._save_profile(conn, profile)

    async def load_profile(self, run_id: str, version: int | None = None) -> Profile | None:
        async with self.db.connection() as conn:
            return await self._load_profile(conn, run_id, version)

    async def update_profile(self, run_id: str, fn: Callable[[Profile], tuple[Profile, T]] | Callable[[Profile], Awaitable[tuple[Profile, T]]]) -> tuple[Profile, T]:
        """Read-modify-write the latest profile under the run's lock. ``fn`` returns (new profile, extra)."""
        async with self.db.transaction() as conn:
            await self._lock_run(conn, run_id)
            profile = await self._load_profile(conn, run_id, None)
            if profile is None:
                raise NotFound(f"no profile for run {run_id}")
            out = fn(profile)
            if isinstance(out, Awaitable):
                out = await out
            new, extra = out  # type: ignore[misc]
            saved = await self._save_profile(conn, new)
            return saved, extra

    async def profile_versions(self, run_id: str) -> list[ProfileVersionRecord]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM profiles WHERE workflow_id = %s ORDER BY version", (run_id,))
            return [ProfileVersionRecord.model_validate(r) for r in await cur.fetchall()]

    async def freeze_profile(self, run_id: str, version: int, *, frozen_by: str) -> ProfileVersionRecord:
        """Freeze a saved version (review submitted): later saves become new versions."""
        async with self.db.transaction() as conn:
            await self._lock_run(conn, run_id)
            cur = await conn.execute(
                """
                UPDATE profiles SET frozen = true, frozen_at = COALESCE(frozen_at, now()), frozen_by = COALESCE(frozen_by, %s)
                WHERE workflow_id = %s AND version = %s RETURNING *
                """,
                (frozen_by, run_id, version),
            )
            row = await cur.fetchone()
        if row is None:
            raise NotFound(f"run {run_id} has no profile version {version}")
        return ProfileVersionRecord.model_validate(row)

    async def latest_facts(self, run_id: str, fact_ids: Iterable[str] | None = None) -> dict[str, Fact]:
        """Latest version of each live fact (the ``facts_latest`` view)."""
        async with self.db.connection() as conn:
            if fact_ids is None:
                cur = await conn.execute("SELECT fact_id, fact FROM facts_latest WHERE workflow_id = %s ORDER BY fact_id", (run_id,))
            else:
                cur = await conn.execute(
                    "SELECT fact_id, fact FROM facts_latest WHERE workflow_id = %s AND fact_id = ANY(%s) ORDER BY fact_id", (run_id, list(fact_ids))
                )
            return {r["fact_id"]: Fact.model_validate(r["fact"]) for r in await cur.fetchall()}

    async def fact_history(self, run_id: str, fact_id: str) -> list[FactVersion]:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "SELECT fact_id, version, profile_version, deleted, fact, created_at FROM facts WHERE workflow_id = %s AND fact_id = %s ORDER BY version",
                (run_id, fact_id),
            )
            return [FactVersion.model_validate(r) for r in await cur.fetchall()]

    async def ops(self, run_id: str, *, after_seq: int = 0) -> list[StateOp]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT op FROM state_ops WHERE workflow_id = %s AND seq > %s ORDER BY seq", (run_id, after_seq))
            return [StateOp.model_validate(r["op"]) for r in await cur.fetchall()]

    async def open_flags(self, run_id: str) -> list[BlockingFlag]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT flag FROM flags WHERE workflow_id = %s AND NOT cleared ORDER BY ord", (run_id,))
            return [BlockingFlag.model_validate(r["flag"]) for r in await cur.fetchall()]

    async def _save_profile(self, conn: Connection, profile: Profile) -> Profile:
        run_id = profile.run_id
        cur = await conn.execute("SELECT version, frozen FROM profiles WHERE workflow_id = %s ORDER BY version DESC LIMIT 1", (run_id,))
        latest = await cur.fetchone()
        if latest is not None:
            if profile.version < latest["version"]:
                raise StaleProfile(f"run {run_id}: saving version {profile.version} but version {latest['version']} is stored")
            if profile.version == latest["version"] and latest["frozen"]:
                profile = profile.model_copy(update={"version": latest["version"] + 1})

        # State ops: append the ones not stored yet.
        cur = await conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM state_ops WHERE workflow_id = %s", (run_id,))
        stored_seq = (await cur.fetchone())["m"]
        new_ops = [op for op in profile.ops_log if op.seq > stored_seq]
        if new_ops:
            async with conn.cursor() as c:
                await c.executemany(
                    "INSERT INTO state_ops (workflow_id, seq, op, actor) VALUES (%s, %s, %s, %s)",
                    [(run_id, op.seq, Jsonb(op.model_dump(mode="json")), op.actor) for op in new_ops],
                )
        last_seq = profile.ops_log[-1].seq if profile.ops_log else 0

        # Facts: a new version for each fact whose content changed; a tombstone for each removed fact.
        cur = await conn.execute(
            """
            SELECT DISTINCT ON (fact_id) fact_id, version, deleted, content_hash
            FROM facts WHERE workflow_id = %s ORDER BY fact_id, version DESC
            """,
            (run_id,),
        )
        stored = {r["fact_id"]: r for r in await cur.fetchall()}
        rows: list[tuple[Any, ...]] = []
        for fid, fact in profile.facts.items():
            data = fact.model_dump(mode="json")
            h = content_hash(data)
            prev = stored.get(fid)
            if prev is None or prev["deleted"] or prev["content_hash"] != h:
                rows.append((run_id, fid, (prev["version"] + 1) if prev else 1, profile.version, Jsonb(data), False, h))
        for fid, prev in stored.items():
            if fid not in profile.facts and not prev["deleted"]:
                rows.append((run_id, fid, prev["version"] + 1, profile.version, None, True, "deleted"))
        if rows:
            async with conn.cursor() as c:
                await c.executemany(
                    "INSERT INTO facts (workflow_id, fact_id, version, profile_version, fact, deleted, content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )

        await self._save_entities(conn, run_id, profile.entities)
        await self._save_flags(conn, run_id, profile.blocking_flags)
        await self._save_followups(conn, run_id, profile.open_questions)
        await conn.execute(
            """
            INSERT INTO profiles (workflow_id, version, call_id, schema_version, anchor_date, last_seq) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (workflow_id, version) DO UPDATE SET last_seq = EXCLUDED.last_seq, updated_at = now()
            """,
            (run_id, profile.version, profile.call_id, profile.schema_version, profile.anchor_date, last_seq),
        )
        return profile

    async def _save_entities(self, conn: Connection, run_id: str, entities: dict[str, Entity]) -> None:
        cur = await conn.execute("SELECT entity_id, ord FROM entities WHERE workflow_id = %s", (run_id,))
        ords = {r["entity_id"]: r["ord"] for r in await cur.fetchall()}
        nxt = max(ords.values(), default=-1) + 1
        rows = []
        for eid, e in entities.items():
            if eid not in ords:
                ords[eid] = nxt
                nxt += 1
            rows.append((run_id, eid, ords[eid], Jsonb(e.model_dump(mode="json"))))
        if rows:
            async with conn.cursor() as c:
                await c.executemany(
                    """
                    INSERT INTO entities (workflow_id, entity_id, ord, entity) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (workflow_id, entity_id) DO UPDATE SET entity = EXCLUDED.entity, updated_at = now()
                    WHERE entities.entity IS DISTINCT FROM EXCLUDED.entity
                    """,
                    rows,
                )
        await conn.execute("DELETE FROM entities WHERE workflow_id = %s AND NOT (entity_id = ANY(%s))", (run_id, list(entities)))

    async def _save_flags(self, conn: Connection, run_id: str, flags: list[BlockingFlag]) -> None:
        rows = [(run_id, f.flag_id, i, Jsonb(f.model_dump(mode="json")), f.cleared, f.cleared_by, f.resolution) for i, f in enumerate(flags)]
        if rows:
            async with conn.cursor() as c:
                await c.executemany(
                    """
                    INSERT INTO flags (workflow_id, flag_id, ord, flag, cleared, cleared_by, resolution) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workflow_id, flag_id) DO UPDATE SET ord = EXCLUDED.ord, flag = EXCLUDED.flag, cleared = EXCLUDED.cleared,
                      cleared_by = EXCLUDED.cleared_by, resolution = EXCLUDED.resolution, updated_at = now()
                    """,
                    rows,
                )
        await conn.execute("DELETE FROM flags WHERE workflow_id = %s AND NOT (flag_id = ANY(%s))", (run_id, [f.flag_id for f in flags]))

    async def _save_followups(self, conn: Connection, run_id: str, followups: list[FollowUp]) -> None:
        if not followups:
            return
        cur = await conn.execute("SELECT followup_id, ord, status FROM followups WHERE workflow_id = %s", (run_id,))
        existing = {r["followup_id"]: r for r in await cur.fetchall()}
        nxt = max((r["ord"] for r in existing.values()), default=-1) + 1
        rows = []
        for q in followups:
            prev = existing.get(q.followup_id)
            ord_ = prev["ord"] if prev else nxt
            if not prev:
                nxt += 1
            # A status recorded through the API (asked, answered, dropped) is never moved back.
            status = prev["status"] if prev and STATUS_RANK[prev["status"]] > STATUS_RANK[q.status] else q.status
            rows.append((run_id, q.followup_id, ord_, Jsonb(q.model_dump(mode="json")), status))
        async with conn.cursor() as c:
            await c.executemany(
                """
                INSERT INTO followups (workflow_id, followup_id, ord, followup, status) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (workflow_id, followup_id) DO UPDATE SET followup = EXCLUDED.followup, status = EXCLUDED.status, updated_at = now()
                """,
                rows,
            )

    async def _load_profile(self, conn: Connection, run_id: str, version: int | None) -> Profile | None:
        if version is None:
            cur = await conn.execute("SELECT * FROM profiles WHERE workflow_id = %s ORDER BY version DESC LIMIT 1", (run_id,))
        else:
            cur = await conn.execute("SELECT * FROM profiles WHERE workflow_id = %s AND version = %s", (run_id, version))
        head = await cur.fetchone()
        if head is None:
            return None
        cur = await conn.execute(
            """
            SELECT fact_id, fact FROM (
              SELECT DISTINCT ON (fact_id) fact_id, fact, deleted FROM facts
              WHERE workflow_id = %s AND profile_version <= %s ORDER BY fact_id, version DESC
            ) f WHERE NOT deleted ORDER BY fact_id
            """,
            (run_id, head["version"]),
        )
        facts = {r["fact_id"]: Fact.model_validate(r["fact"]) for r in await cur.fetchall()}
        cur = await conn.execute("SELECT op FROM state_ops WHERE workflow_id = %s AND seq <= %s ORDER BY seq", (run_id, head["last_seq"]))
        ops = [StateOp.model_validate(r["op"]) for r in await cur.fetchall()]
        cur = await conn.execute("SELECT entity_id, entity FROM entities WHERE workflow_id = %s ORDER BY ord", (run_id,))
        entities = {r["entity_id"]: Entity.model_validate(r["entity"]) for r in await cur.fetchall()}
        cur = await conn.execute("SELECT flag FROM flags WHERE workflow_id = %s ORDER BY ord", (run_id,))
        flags = [BlockingFlag.model_validate(r["flag"]) for r in await cur.fetchall()]
        cur = await conn.execute("SELECT followup, status FROM followups WHERE workflow_id = %s ORDER BY ord", (run_id,))
        questions = [FollowUp.model_validate({**r["followup"], "status": r["status"]}) for r in await cur.fetchall()]
        return Profile(
            call_id=head["call_id"],
            run_id=run_id,
            schema_version=head["schema_version"],
            anchor_date=head["anchor_date"],
            entities=entities,
            facts=facts,
            ops_log=ops,
            open_questions=questions,
            blocking_flags=flags,
            version=head["version"],
        )

    # ------------------------------------------------------------------ follow-ups

    async def list_followups(self, run_id: str) -> list[FollowupRecord]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM followups WHERE workflow_id = %s ORDER BY ord", (run_id,))
            rows = await cur.fetchall()
        return [
            FollowupRecord(
                workflow_id=r["workflow_id"],
                followup=FollowUp.model_validate({**r["followup"], "status": r["status"]}),
                status=r["status"],
                answer=FollowupAnswer.model_validate(r["answer"]) if r["answer"] else None,
                answered_at=r["answered_at"],
            )
            for r in rows
        ]

    async def get_followup(self, run_id: str, followup_id: str) -> FollowupRecord | None:
        return next((r for r in await self.list_followups(run_id) if r.followup.followup_id == followup_id), None)

    async def record_followup_answer(self, run_id: str, answer: FollowupAnswer) -> FollowupRecord:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE followups SET status = 'answered', answer = %s, answered_at = %s, updated_at = now()
                WHERE workflow_id = %s AND followup_id = %s RETURNING workflow_id
                """,
                (Jsonb(answer.model_dump(mode="json")), answer.answered_at, run_id, answer.followup_id),
            )
            if await cur.fetchone() is None:
                raise NotFound(f"run {run_id} has no follow-up {answer.followup_id}")
        rec = await self.get_followup(run_id, answer.followup_id)
        assert rec is not None
        return rec

    async def set_followup_status(self, run_id: str, followup_id: str, status: str) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE followups SET status = %s, updated_at = now() WHERE workflow_id = %s AND followup_id = %s", (status, run_id, followup_id)
            )

    async def apply_followup_edit(self, run_id: str, edit: FollowupEdit, *, new_id: str | None = None) -> str | None:
        """Apply an adviser's follow-up edit (§11.2): reword, drop or add. Returns the follow-up id touched."""
        async with self.db.transaction() as conn:
            if edit.action == "add":
                fid = new_id or f"fu.adviser.{content_hash([edit.question, edit.at.isoformat()])[:10]}"
                q = FollowUp(followup_id=fid, fact_ids=[], question=edit.question, why="added by the adviser", expected_pos_change=0.0, answer_type="text")
                cur = await conn.execute("SELECT COALESCE(MAX(ord), -1) + 1 AS o FROM followups WHERE workflow_id = %s", (run_id,))
                ord_ = (await cur.fetchone())["o"]
                await conn.execute(
                    "INSERT INTO followups (workflow_id, followup_id, ord, followup, status) VALUES (%s, %s, %s, %s, 'open') ON CONFLICT DO NOTHING",
                    (run_id, fid, ord_, Jsonb(q.model_dump(mode="json"))),
                )
                return fid
            if not edit.followup_id:
                return None
            if edit.action == "drop":
                await conn.execute(
                    "UPDATE followups SET status = 'dropped', updated_at = now() WHERE workflow_id = %s AND followup_id = %s", (run_id, edit.followup_id)
                )
            else:
                await conn.execute(
                    """
                    UPDATE followups SET followup = jsonb_set(followup, '{question}', to_jsonb(%s::text)), updated_at = now()
                    WHERE workflow_id = %s AND followup_id = %s
                    """,
                    (edit.question, run_id, edit.followup_id),
                )
            return edit.followup_id

    # ------------------------------------------------------------------ agent checkpoints (§13.2)

    async def load_checkpoint(self, run_id: str, scope: str = "") -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "SELECT turn, messages, harness_state FROM agent_checkpoints WHERE workflow_id = %s AND scope = %s", (run_id, scope)
            )
            return await cur.fetchone()

    async def save_checkpoint(self, run_id: str, scope: str, turn: int, messages: list[Any], harness_state: dict[str, Any]) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO agent_checkpoints (workflow_id, scope, turn, messages, harness_state) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (workflow_id, scope) DO UPDATE SET turn = EXCLUDED.turn, messages = EXCLUDED.messages,
                  harness_state = EXCLUDED.harness_state, updated_at = now()
                WHERE agent_checkpoints.turn <= EXCLUDED.turn
                """,
                (run_id, scope, turn, Jsonb(messages), Jsonb(harness_state)),
            )

    async def clear_checkpoints(self, run_id: str) -> None:
        async with self.db.connection() as conn:
            await conn.execute("DELETE FROM agent_checkpoints WHERE workflow_id = %s", (run_id,))

    # ------------------------------------------------------------------ review

    async def record_review_events(self, events: list[ReviewEvent]) -> int:
        if not events:
            return 0
        async with self.db.connection() as conn:
            async with conn.cursor() as c:
                await c.executemany(
                    """
                    INSERT INTO review_events (workflow_id, fact_id, action, reason, reviewer_id, at, op_seq, profile_version, detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [(e.workflow_id, e.fact_id, e.action, e.reason, e.reviewer_id, e.at, e.op_seq, e.profile_version, Jsonb(e.detail)) for e in events],
                )
        return len(events)

    async def review_events(self, *, run_id: str | None = None, since: datetime | None = None, until: datetime | None = None) -> list[ReviewEvent]:
        where, params = ["true"], []
        if run_id is not None:
            where.append("workflow_id = %s")
            params.append(run_id)
        if since is not None:
            where.append("at >= %s")
            params.append(since)
        if until is not None:
            where.append("at < %s")
            params.append(until)
        async with self.db.connection() as conn:
            cur = await conn.execute(f"SELECT * FROM review_events WHERE {' AND '.join(where)} ORDER BY at, id", params)  # noqa: S608 - fixed clauses
            return [ReviewEvent.model_validate(r) for r in await cur.fetchall()]

    async def save_review_result(self, run_id: str, result: ReviewResult) -> int:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO review_results (workflow_id, reviewer_id, base_profile_version, submitted, result)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (run_id, result.reviewer_id, result.base_profile_version, result.submitted, Jsonb(result.model_dump(mode="json"))),
            )
            return (await cur.fetchone())["id"]

    async def review_results(self, run_id: str) -> list[ReviewResultRecord]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM review_results WHERE workflow_id = %s ORDER BY id", (run_id,))
            return [ReviewResultRecord.model_validate(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------ forecasts

    async def save_forecast(self, run_id: str, profile_version: int, mode: str, result: ForecastResult, *, config_version: str, cma_version: str) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO forecasts (workflow_id, profile_version, config_version, cma_version, mode, result) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (workflow_id, profile_version, config_version, cma_version, mode) DO UPDATE SET result = EXCLUDED.result, created_at = now()
                """,
                (run_id, profile_version, config_version, cma_version, mode, Jsonb(result.model_dump(mode="json"))),
            )

    async def get_forecast(self, run_id: str, *, mode: str | None = None, profile_version: int | None = None) -> ForecastRecord | None:
        """The newest forecast for the run (optionally of one mode or profile version)."""
        where, params = ["workflow_id = %s"], [run_id]
        if mode is not None:
            where.append("mode = %s")
            params.append(mode)
        if profile_version is not None:
            where.append("profile_version = %s")
            params.append(profile_version)
        async with self.db.connection() as conn:
            cur = await conn.execute(
                f"SELECT * FROM forecasts WHERE {' AND '.join(where)} ORDER BY profile_version DESC, created_at DESC LIMIT 1", params  # noqa: S608
            )
            row = await cur.fetchone()
        return ForecastRecord.model_validate(row) if row else None

    # ------------------------------------------------------------------ clients: prior documents and CRM

    async def save_client_document(self, client_id: str, document: FnaDocument, *, source: str, call_id: str | None = None) -> int:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO client_documents (client_id, document, source, call_id) VALUES (%s, %s, %s, %s) RETURNING id",
                (client_id, Jsonb(document.model_dump(mode="json")), source, call_id),
            )
            return (await cur.fetchone())["id"]

    async def latest_client_document(self, client_id: str, *, before_id: int | None = None) -> FnaDocument | None:
        found = await self.latest_client_document_id(client_id, before_id=before_id)
        return await self.client_document(found) if found is not None else None

    async def latest_client_document_id(self, client_id: str, *, before_id: int | None = None) -> int | None:
        async with self.db.connection() as conn:
            if before_id is None:
                cur = await conn.execute("SELECT id FROM client_documents WHERE client_id = %s ORDER BY id DESC LIMIT 1", (client_id,))
            else:
                cur = await conn.execute("SELECT id FROM client_documents WHERE client_id = %s AND id < %s ORDER BY id DESC LIMIT 1", (client_id, before_id))
            row = await cur.fetchone()
        return row["id"] if row else None

    async def client_document(self, document_id: int) -> FnaDocument | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT document FROM client_documents WHERE id = %s", (document_id,))
            row = await cur.fetchone()
        return FnaDocument.model_validate(row["document"]) if row else None

    async def save_crm_record(self, record: CrmRecord) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO client_crm (client_id, record) VALUES (%s, %s)
                ON CONFLICT (client_id) DO UPDATE SET record = EXCLUDED.record, updated_at = now()
                """,
                (record.client_id, Jsonb(record.model_dump(mode="json"))),
            )

    async def get_crm_record(self, client_id: str) -> CrmRecord | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT record FROM client_crm WHERE client_id = %s", (client_id,))
            row = await cur.fetchone()
        return CrmRecord.model_validate(row["record"]) if row else None

    # ------------------------------------------------------------------ calibrators and lexicons (§9.4, §11.4)

    async def register_calibrator(self, rec: CalibratorRecord) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO calibrators (version, slot_group, artifact_uri, metrics, tau, alpha, n_calib_calls, promoted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version, slot_group) DO UPDATE SET artifact_uri = COALESCE(EXCLUDED.artifact_uri, calibrators.artifact_uri),
                  metrics = calibrators.metrics || EXCLUDED.metrics, tau = COALESCE(EXCLUDED.tau, calibrators.tau),
                  alpha = COALESCE(EXCLUDED.alpha, calibrators.alpha), n_calib_calls = COALESCE(EXCLUDED.n_calib_calls, calibrators.n_calib_calls),
                  promoted = EXCLUDED.promoted OR calibrators.promoted
                """,
                (rec.version, rec.slot_group, rec.artifact_uri, Jsonb(rec.metrics), rec.tau, rec.alpha, rec.n_calib_calls, rec.promoted),
            )

    async def calibrators(self, *, version: str | None = None, promoted_only: bool = False) -> list[CalibratorRecord]:
        where, params = ["true"], []
        if version is not None:
            where.append("version = %s")
            params.append(version)
        if promoted_only:
            where.append("promoted")
        async with self.db.connection() as conn:
            cur = await conn.execute(f"SELECT * FROM calibrators WHERE {' AND '.join(where)} ORDER BY created_at DESC, slot_group", params)  # noqa: S608
            return [CalibratorRecord.model_validate(r) for r in await cur.fetchall()]

    async def current_calibrator_version(self) -> str | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT version FROM calibrators WHERE promoted ORDER BY created_at DESC LIMIT 1")
            row = await cur.fetchone()
        return row["version"] if row else None

    async def save_lexicon(self, version: str, yaml_text: str, *, approved_by: str | None = None) -> None:
        """Lexicon versions are immutable: saving different text under an existing version is an error."""
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO lexicons (version, yaml, approved_by) VALUES (%s, %s, %s) ON CONFLICT (version) DO NOTHING RETURNING version",
                (version, yaml_text, approved_by),
            )
            if await cur.fetchone() is None:
                cur = await conn.execute("SELECT yaml FROM lexicons WHERE version = %s", (version,))
                if (await cur.fetchone())["yaml"] != yaml_text:
                    raise ValueError(f"lexicon {version} already exists with different content")

    async def get_lexicon(self, version: str) -> str | None:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT yaml FROM lexicons WHERE version = %s", (version,))
            row = await cur.fetchone()
        return row["yaml"] if row else None

    # ------------------------------------------------------------------ notifications (idempotent sends)

    async def claim_notification(self, idem_key: str, *, run_id: str, kind: str, recipient: str | None, payload: dict[str, Any]) -> NotificationRecord:
        """The notification row for ``idem_key``, created if new. Send only when ``sent_at`` is None."""
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO notifications (idem_key, workflow_id, kind, recipient, payload) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (idem_key) DO NOTHING
                """,
                (idem_key, run_id, kind, recipient, Jsonb(payload)),
            )
            cur = await conn.execute("SELECT * FROM notifications WHERE idem_key = %s", (idem_key,))
            return NotificationRecord.model_validate(await cur.fetchone())

    async def mark_notification(self, idem_key: str, *, channel: str, error: str | None = None) -> None:
        async with self.db.connection() as conn:
            if error is None:
                await conn.execute("UPDATE notifications SET channel = %s, sent_at = now(), error = NULL WHERE idem_key = %s", (channel, idem_key))
            else:
                await conn.execute("UPDATE notifications SET channel = %s, error = %s WHERE idem_key = %s", (channel, error, idem_key))

    async def notifications(self, run_id: str) -> list[NotificationRecord]:
        async with self.db.connection() as conn:
            cur = await conn.execute("SELECT * FROM notifications WHERE workflow_id = %s ORDER BY created_at, idem_key", (run_id,))
            return [NotificationRecord.model_validate(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------ meeting notes (§11.6)

    async def save_note(self, run_id: str, profile_version: int, note: dict[str, Any]) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO meeting_notes (workflow_id, profile_version, note) VALUES (%s, %s, %s)
                ON CONFLICT (workflow_id, profile_version) DO UPDATE SET note = EXCLUDED.note, created_at = now()
                """,
                (run_id, profile_version, Jsonb(note)),
            )

    async def latest_note(self, run_id: str) -> tuple[int, dict[str, Any]] | None:
        async with self.db.connection() as conn:
            cur = await conn.execute(
                "SELECT profile_version, note FROM meeting_notes WHERE workflow_id = %s ORDER BY profile_version DESC LIMIT 1", (run_id,)
            )
            row = await cur.fetchone()
        return (row["profile_version"], row["note"]) if row else None
