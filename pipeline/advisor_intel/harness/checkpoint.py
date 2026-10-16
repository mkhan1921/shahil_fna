"""Per-turn checkpoints so a Temporal retry of ``run_agent`` resumes from the last turn (spec §13.2).

The store is a protocol: the activity layer provides the Postgres implementation
(``agent_checkpoints`` table); tests and in-process runs use ``InMemoryCheckpointStore``.
Messages are stored as Pydantic AI's JSON message format.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter


class Checkpoint(BaseModel):
    turn: int
    messages: list[dict[str, Any]]
    harness_state: dict[str, Any]


class CheckpointStore(Protocol):
    async def load(self, run_id: str) -> Checkpoint | None: ...

    async def save(self, run_id: str, turn: int, messages: list[dict[str, Any]], harness_state: dict[str, Any]) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self.data: dict[str, Checkpoint] = {}
        self.saves = 0

    async def load(self, run_id: str) -> Checkpoint | None:
        return self.data.get(run_id)

    async def save(self, run_id: str, turn: int, messages: list[dict[str, Any]], harness_state: dict[str, Any]) -> None:
        self.saves += 1
        self.data[run_id] = Checkpoint(turn=turn, messages=messages, harness_state=harness_state)


def dump_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def load_messages(data: list[dict[str, Any]]) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(data)
