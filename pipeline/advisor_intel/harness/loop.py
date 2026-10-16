"""Loop detection (spec §13.7): the same tool with the same arguments three times in the last ten
calls gets an error result instead of running again."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

WINDOW = 10
REPEATS = 3


def args_hash(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()[:16]


class LoopDetector:
    def __init__(self, history: list[str] | None = None) -> None:
        self.recent: deque[str] = deque(history or [], maxlen=WINDOW)

    def observe(self, tool: str, args: dict[str, Any]) -> bool:
        """Record a call; False when it is the third identical call in the window (block it)."""
        h = args_hash(tool, args)
        repeats = sum(1 for x in self.recent if x == h)
        self.recent.append(h)
        return repeats + 1 < REPEATS
