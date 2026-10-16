"""The model-swap protocol (spec §15.4).

The gold set is the fixed point; models are interchangeable. A candidate model is pinned in place of the current
one for a single role, the gold suite runs with it, and the candidate wins only if it is at least as good on every
gated metric of that role's stage, better on at least one, at equal or lower cost per call-minute. A win writes the
routing change (``pipeline/routing.yaml``) and opens a pull request that switches it; the deployment maps the file
to the ``ADVISOR_INTEL_MODEL_<ROLE>`` settings that ``config.py`` reads. The gold calls, scorers and gates do not
change. ``run_model_swap`` is the entry point the monthly Temporal schedule calls.
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import httpx
import yaml
from pydantic import BaseModel

from ..config import PIPELINE_DIR
from ..models.runs import VersionPins
from .datasets import GOLD_CALLS, DatasetStore, Stage12Builder, Stage12Cache, get_store
from .extraction_eval import EvalReport, Runner, current_version_pins, eval_cases, run_local

ROUTING_FILE = PIPELINE_DIR / "routing.yaml"
REPO_ROUTING_PATH = "pipeline/routing.yaml"  # the same file, as a path in the repository
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


def routing_text(decision: SwapDecision, current: str | None = None) -> str:
    """The routing file after a winning swap: role → model, with the history of switches (§13.8, §15.4)."""
    if not decision.wins:
        raise ValueError(f"the candidate did not win: {decision.reason}")
    data = (yaml.safe_load(current) if current else None) or {"roles": {}, "history": []}
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
    return (
        "# Model routing per role (spec §13.8), changed by the model-swap protocol (§15.4) through pull requests.\n"
        "# Deployments export each role as ADVISOR_INTEL_MODEL_<ROLE> (read by advisor_intel.config).\n"
        + yaml.safe_dump(data, sort_keys=False)
    )


def write_routing_change(decision: SwapDecision, path: Path = ROUTING_FILE) -> Path:
    """Record a winning swap in the local routing file (role → model, with history) for the pull request."""
    path.write_text(routing_text(decision, path.read_text() if path.exists() else None))
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


# --------------------------------------------------------------------------- the monthly run


class GitHubPullRequests:
    """Opens the routing-change pull request through the GitHub REST API, so the worker needs no checkout.

    Configured by ``GITHUB_REPOSITORY`` (owner/repo) and ``GITHUB_TOKEN`` (or ``GH_TOKEN``); the base branch is
    ``ADVISOR_INTEL_MODEL_SWAP_BASE`` (default main). Unconfigured, it opens nothing.
    """

    def __init__(
        self,
        repo: str | None = None,
        token: str | None = None,
        *,
        base: str | None = None,
        client: httpx.Client | None = None,
        api_url: str = "https://api.github.com",
    ) -> None:
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY")
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base = base or os.environ.get("ADVISOR_INTEL_MODEL_SWAP_BASE", "main")
        self._client = client
        self.api_url = api_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.repo and self.token)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        return self._http().request(method, f"{self.api_url}/repos/{self.repo}{path}", headers=headers, **kw)

    def branch_name(self, decision: SwapDecision) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", decision.candidate_model.lower()).strip("-")
        return f"model-swap/{decision.role}-{slug}-{(decision.decided_at or datetime.now(timezone.utc).isoformat())[:10]}"

    def __call__(self, decision: SwapDecision, routing: str | None = None) -> str | None:
        """Branch from the base, commit the routing change on it and open the pull request; returns its URL."""
        if not self.configured:
            return None
        branch = self.branch_name(decision)
        r = self._request("GET", f"/git/ref/heads/{self.base}")
        r.raise_for_status()
        r = self._request("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": r.json()["object"]["sha"]})
        if r.status_code != 422:  # 422: the branch exists (a re-run of the same decision); update it
            r.raise_for_status()
        r = self._request("GET", f"/contents/{REPO_ROUTING_PATH}", params={"ref": branch})
        current, sha = None, None
        if r.status_code == 200:
            body = r.json()
            current, sha = base64.b64decode(body["content"]).decode(), body["sha"]
        elif r.status_code != 404:
            r.raise_for_status()
        text = routing if routing is not None else routing_text(decision, current)
        put: dict[str, Any] = {
            "message": f"Switch the {decision.role} role to {decision.candidate_model} (model-swap protocol, §15.4)",
            "content": base64.b64encode(text.encode()).decode(),
            "branch": branch,
        }
        if sha:
            put["sha"] = sha
        self._request("PUT", f"/contents/{REPO_ROUTING_PATH}", json=put).raise_for_status()
        r = self._request(
            "POST",
            "/pulls",
            json={
                "title": f"Model swap: {decision.role} → {decision.candidate_model}",
                "head": branch,
                "base": self.base,
                "body": decision.markdown(),
            },
        )
        if r.status_code == 422:  # a pull request for this branch is already open
            r = self._request("GET", "/pulls", params={"head": f"{self.repo.split('/')[0]}:{branch}", "state": "open"})
            r.raise_for_status()
            return r.json()[0]["html_url"] if r.json() else None
        r.raise_for_status()
        return r.json()["html_url"]


def gold_suite(
    *,
    settings: Any = None,
    store: DatasetStore | None = None,
    runner: Runner | None = None,
    cache: Stage12Cache | None = None,
    build: Stage12Builder | None = None,
    transcript_kinds: Sequence[str] | None = None,
) -> Callable[[VersionPins], EvalReport]:
    """The gold suite as ``run_eval``: every gold call (human and ASR transcripts) through stages 3–6 with the given
    pins, from the Stage 1–2 cache (an ASR swap misses the cache and rebuilds Stage 1–2 through ``build``)."""
    cases = eval_cases(GOLD_CALLS, store=store or get_store(settings=settings), transcript_kinds=transcript_kinds)
    if not cases:
        raise ValueError("the gold-calls dataset is empty: the model-swap protocol needs the gold suite")

    def run(pins: VersionPins) -> EvalReport:
        return run_local(cases, runner=runner, pins=pins, cache=cache, build=build, dataset=GOLD_CALLS, experiment="model-swap", save=False)

    return run


def run_model_swap(
    candidates: Mapping[str, str],
    *,
    settings: Any = None,
    pins: VersionPins | None = None,
    run_eval: Callable[[VersionPins], EvalReport] | None = None,
    open_pull_request: Callable[[SwapDecision], str | None] | None = None,
    routing_path: Path = ROUTING_FILE,
) -> dict[str, Any]:
    """The monthly model-swap run (§15.4): for each role → candidate model, run the gold suite with the candidate
    pinned in place of the current model, decide, and for a win open a pull request that switches it.

    The current pins run once and serve as the baseline for every role. Without a pull-request opener (no GitHub
    repository and token) a win is written to the local routing file instead. Returns ``{"results": [one decision
    per role], "pull_requests": [urls]}``.
    """
    pins = pins or current_version_pins()
    run_eval = run_eval or gold_suite(settings=settings)
    opener = open_pull_request if open_pull_request is not None else GitHubPullRequests()
    configured = getattr(opener, "configured", True)
    baseline: EvalReport | None = None
    results: list[dict[str, Any]] = []
    prs: list[str] = []
    for role, model in candidates.items():
        row: dict[str, Any] = {"role": role, "candidate_model": model}
        if role not in ROLE_PINS:
            results.append(row | {"skipped": f"unknown role; one of {sorted(ROLE_PINS)}"})
            continue
        if str(getattr(pins, ROLE_PINS[role])) == model:
            results.append(row | {"skipped": "already the current model"})
            continue
        baseline = baseline or run_eval(pins)
        decision = evaluate_swap(role, model, pins=pins, run_eval=run_eval, baseline=baseline)
        row = decision.model_dump(mode="json")
        if decision.wins:
            if configured:
                url = opener(decision)
                if url:
                    prs.append(url)
                    row["pull_request"] = url
            else:
                row["routing_file"] = str(write_routing_change(decision, routing_path))
        results.append(row)
    return {"results": results, "pull_requests": prs}
