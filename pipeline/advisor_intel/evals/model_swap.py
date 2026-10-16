"""The model-swap protocol (spec §15.4).

The gold set is the fixed point; models are interchangeable. A candidate model is pinned in place of the current
one for a single role, the gold suite runs with it, and the candidate wins only if it is at least as good on every
gated metric of that role's stage, better on at least one, at equal or lower cost per call-minute. A win writes the
routing change (``pipeline/routing.yaml``) and a pull-request body; the deployment maps the file to the
``ADVISOR_INTEL_MODEL_<ROLE>`` settings that ``config.py`` reads. The gold calls, scorers and gates do not change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import yaml
from pydantic import BaseModel

from ..config import PIPELINE_DIR
from ..models.runs import VersionPins
from .extraction_eval import EvalReport

ROUTING_FILE = PIPELINE_DIR / "routing.yaml"
WIN_EPSILON = 0.005  # differences smaller than this count as ties (noise on 40–100 gold calls)

Role = Literal["frontier", "reasoning", "verifier", "small", "embedding", "asr"]

# The VersionPins field a role's model lives in.
ROLE_PINS: dict[str, str] = {
    "frontier": "frontier_model",
    "reasoning": "reasoning_model",
    "verifier": "verifier_model",
    "small": "small_model",
    "embedding": "embedding_model",
    "asr": "asr_model_version",
}

LOWER_IS_BETTER = frozenset(
    {"ece", "brier", "wrong_auto_accept_share", "pos_error_median", "pos_error_p90", "critical_miss_rate", "cost_per_call_minute"}
)

# The gated metrics of each role's stage (§13.8 roles, §15.3 metrics).
ROLE_METRICS: dict[str, tuple[str, ...]] = {
    "reasoning": ("status_accuracy_tier_a", "slot_recall_tier_a", "value_coverage_tier_a", "grounding_tier_a", "attribution_tier_a", "ece"),
    "verifier": ("ece", "brier", "wrong_auto_accept_share", "grounding_tier_a"),
    "frontier": ("status_accuracy_tier_a", "slot_recall_tier_a", "mention_accuracy", "pos_error_median"),
    "small": ("topic_recall", "mention_accuracy", "slot_recall_tier_a"),
    "embedding": ("topic_recall", "slot_recall_tier_a"),
    "asr": ("numeric_word_accuracy", "diarization_accuracy", "role_accuracy", "value_coverage_tier_a"),
}


class SwapRow(BaseModel):
    metric: str
    current: float | None
    candidate: float | None
    better: bool
    worse: bool


class SwapDecision(BaseModel):
    role: str
    current_model: str
    candidate_model: str
    wins: bool
    reason: str
    rows: list[SwapRow]
    cost_current: float | None = None
    cost_candidate: float | None = None
    decided_at: str = ""

    def markdown(self) -> str:
        lines = [
            f"## Model swap: `{self.role}` → `{self.candidate_model}`",
            "",
            f"Current: `{self.current_model}`. Decision: **{'switch' if self.wins else 'keep'}** — {self.reason}.",
            "",
            "| metric | current | candidate |",
            "|---|---:|---:|",
        ]
        for r in self.rows:
            mark = " ⬆" if r.better else (" ⬇" if r.worse else "")
            lines.append(f"| `{r.metric}` | {_fmt(r.current)} | {_fmt(r.candidate)}{mark} |")
        lines.append(f"| cost per call-minute (USD) | {_fmt(self.cost_current)} | {_fmt(self.cost_candidate)} |")
        return "\n".join(lines)


def _fmt(x: float | None) -> str:
    return "–" if x is None else f"{x:.4f}"


def candidate_pins(pins: VersionPins, role: str, model: str) -> VersionPins:
    """The run's pins with ``model`` in place of the current one for ``role``."""
    return pins.model_copy(update={ROLE_PINS[role]: model})


def decide(
    role: str,
    current: EvalReport,
    candidate: EvalReport,
    *,
    current_model: str,
    candidate_model: str,
    metrics: tuple[str, ...] | None = None,
) -> SwapDecision:
    """Compare two reports on the role's gated metrics and cost (§15.4)."""
    rows = []
    for m in metrics or ROLE_METRICS[role]:
        c, k = current.metric(m), candidate.metric(m)
        if c is None or k is None:
            rows.append(SwapRow(metric=m, current=c, candidate=k, better=False, worse=c is not None and k is None))
            continue
        d = (c - k) if m in LOWER_IS_BETTER else (k - c)
        rows.append(SwapRow(metric=m, current=c, candidate=k, better=d > WIN_EPSILON, worse=d < -WIN_EPSILON))
    cost_c, cost_k = current.metric("cost_per_call_minute"), candidate.metric("cost_per_call_minute")
    measured = [r for r in rows if r.current is not None and r.candidate is not None]
    if not measured:
        wins, reason = False, "no gated metric was measured for both models"
    elif any(r.worse for r in rows):
        wins, reason = False, "worse on " + ", ".join(r.metric for r in rows if r.worse)
    elif not any(r.better for r in rows):
        wins, reason = False, "no gated metric improved"
    elif cost_c is not None and cost_k is not None and cost_k > cost_c * (1 + 1e-9):
        wins, reason = False, f"better but more expensive ({cost_k:.4f} vs {cost_c:.4f} USD per call-minute)"
    else:
        wins, reason = True, "better on " + ", ".join(r.metric for r in rows if r.better) + " at equal or lower cost"
    return SwapDecision(
        role=role,
        current_model=current_model,
        candidate_model=candidate_model,
        wins=wins,
        reason=reason,
        rows=rows,
        cost_current=cost_c,
        cost_candidate=cost_k,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )


def evaluate_swap(
    role: str,
    candidate_model: str,
    *,
    pins: VersionPins,
    run_eval: Callable[[VersionPins], EvalReport],
    baseline: EvalReport | None = None,
) -> SwapDecision:
    """Run the gold suite with the candidate pinned for ``role`` (and with the current pins unless a baseline report
    is given) and decide the swap."""
    if role not in ROLE_PINS:
        raise ValueError(f"unknown role {role!r}; one of {sorted(ROLE_PINS)}")
    current_model = str(getattr(pins, ROLE_PINS[role]))
    current = baseline or run_eval(pins)
    candidate = run_eval(candidate_pins(pins, role, candidate_model))
    return decide(role, current, candidate, current_model=current_model, candidate_model=candidate_model)


def write_routing_change(decision: SwapDecision, path: Path = ROUTING_FILE) -> Path:
    """Record a winning swap in the routing file (role → model, with history) for the pull request."""
    if not decision.wins:
        raise ValueError(f"the candidate did not win: {decision.reason}")
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    data = data or {"roles": {}, "history": []}
    data.setdefault("roles", {})[decision.role] = decision.candidate_model
    data.setdefault("history", []).append(
        {
            "role": decision.role,
            "from": decision.current_model,
            "to": decision.candidate_model,
            "at": decision.decided_at,
            "reason": decision.reason,
        }
    )
    path.write_text(
        "# Model routing per role (spec §13.8), changed by the model-swap protocol (§15.4) through pull requests.\n"
        "# Deployments export each role as ADVISOR_INTEL_MODEL_<ROLE> (read by advisor_intel.config).\n"
        + yaml.safe_dump(data, sort_keys=False)
    )
    return path


def routing_env(path: Path = ROUTING_FILE) -> dict[str, str]:
    """The routing file as the environment variables ``config.get_settings`` reads."""
    if not path.exists():
        return {}
    roles = (yaml.safe_load(path.read_text()) or {}).get("roles", {})
    return {
        f"ADVISOR_INTEL_MODEL_{role.upper()}": str(model)
        for role, model in roles.items()
        if role in ("frontier", "reasoning", "verifier", "small")
    }
