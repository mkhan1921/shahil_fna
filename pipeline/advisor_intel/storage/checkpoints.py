"""Per-turn agent checkpoints in Postgres, so a Temporal retry of ``run_agent`` resumes (spec §13.2).

Implements the harness's ``CheckpointStore`` protocol (``advisor_intel.harness.checkpoint``)::

    async load(run_id) -> Checkpoint | None
    async save(run_id, turn, messages, harness_state) -> None

``messages`` is Pydantic AI's JSON message history (``dump_messages``) and ``harness_state`` the
harness's JSON-able state (plan, budgets, notes, loop detector, and the profile on resume).

A store is scoped to one Temporal execution: retries of the activity within the execution share
the scope and resume; a workflow reset starts a new execution, hence a clean agent run.
"""

from __future__ import annotations

from typing import Any

from ..harness.checkpoint import Checkpoint
from .store import Store

__all__ = ["Checkpoint", "PostgresCheckpointStore"]


class PostgresCheckpointStore:
    def __init__(self, store: Store, *, scope: str = "") -> None:
        self.store = store
        self.scope = scope
        self.saves = 0

    async def load(self, run_id: str) -> Checkpoint | None:
        row = await self.store.load_checkpoint(run_id, self.scope)
        if row is None:
            return None
        return Checkpoint(turn=row["turn"], messages=list(row["messages"] or []), harness_state=dict(row["harness_state"] or {}))

    async def save(self, run_id: str, turn: int, messages: list[dict[str, Any]], harness_state: dict[str, Any]) -> None:
        await self.store.save_checkpoint(run_id, self.scope, int(turn), list(messages), dict(harness_state))
        self.saves += 1

    async def clear(self, run_id: str) -> None:
        await self.store.clear_checkpoints(run_id)
