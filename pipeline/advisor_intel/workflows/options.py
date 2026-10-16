"""Activity options per the §13.1 table: timeouts, heartbeats and retries.

"Retries" in the table counts retries after the first attempt, so ``maximum_attempts`` is
retries + 1. Backoff is exponential (2×) from a short initial interval. Errors raised as
non-retryable ``ApplicationError``s (bad input, a missing call) fail at once.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio.common import RetryPolicy


def _opts(timeout: timedelta, retries: int, heartbeat: timedelta | None = None, *, initial: timedelta = timedelta(seconds=5)) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start_to_close_timeout": timeout,
        "retry_policy": RetryPolicy(
            initial_interval=initial,
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=5),
            maximum_attempts=retries + 1,
            non_retryable_error_types=["InputError", "NotFound"],
        ),
    }
    if heartbeat is not None:
        out["heartbeat_timeout"] = heartbeat
    return out


# | Activity               | Timeout | Heartbeat | Retries |
INGEST_OPTS = _opts(timedelta(minutes=30), 3, timedelta(seconds=30), initial=timedelta(seconds=10))
DISCOURSE_OPTS = _opts(timedelta(minutes=20), 3, timedelta(seconds=60))
AGENT_OPTS = _opts(timedelta(minutes=25), 2, timedelta(minutes=2))  # resumes from the last per-turn checkpoint
VOI_OPTS = _opts(timedelta(minutes=10), 3, timedelta(seconds=60))
FORECAST_OPTS = _opts(timedelta(minutes=10), 3, timedelta(seconds=60))
REVIEW_OPTS = _opts(timedelta(minutes=2), 5)
NOTIFY_OPTS = _opts(timedelta(minutes=1), 5)

# Not in the table: follow-up intake runs one extraction (like a small discourse step); the meeting
# note is one model call; nightly jobs are batch work over the day's review events.
INTAKE_OPTS = _opts(timedelta(minutes=10), 3, timedelta(seconds=60))
NOTE_OPTS = _opts(timedelta(minutes=5), 3)
LABELS_OPTS = _opts(timedelta(minutes=15), 3, timedelta(seconds=60))
RETRAIN_OPTS = _opts(timedelta(minutes=45), 2, timedelta(minutes=2))
CONFORMAL_OPTS = _opts(timedelta(minutes=15), 3, timedelta(seconds=60))
LEXICON_FIT_OPTS = _opts(timedelta(minutes=20), 2, timedelta(minutes=2))
DATASET_SYNC_OPTS = _opts(timedelta(minutes=15), 3, timedelta(seconds=60))
MODEL_SWAP_OPTS = _opts(timedelta(hours=4), 1, timedelta(minutes=5))
