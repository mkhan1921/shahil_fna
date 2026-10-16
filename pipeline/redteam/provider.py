"""promptfoo Python provider: the advisor call pipeline on a red-team scenario (spec §15.7).

promptfoo calls ``call_api(prompt, options, context)`` once per test. The prompt is a scenario name from
``advisor_intel.evals.redteam`` (``hedges``, ``injection_instructions``, …); any other text is treated as an attack and
spliced into a base scenario (``vars.base``, default ``injection_instructions``) as one more client utterance, so
generated attacks reach the pipeline through the same transcript path and are never evidence.

The provider runs the whole pipeline on the scenario's scripted transcript and returns JSON with the §15.7 verdict
(``criterion``), the scenario's expectation failures, the facts and the tool calls; ``asserts.py`` judges it.

Provider config (``promptfooconfig.yaml``):

- ``pins``: ``current`` (default: the deployment's models and prompt versions, from settings), ``test`` (offline
  schema fakes), or a mapping of ``VersionPins`` fields laid over the current pins;
- ``model``: ``oracle`` or ``injection_follower`` replaces the extraction model, to check the harness itself offline;
- ``trace``: capture the run's spans so tool calls are checked against the whole §13.4 policy (default true).

Run with ``PROMPTFOO_PYTHON`` pointing at the pipeline's virtualenv interpreter.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

DEFAULT_BASE = "injection_instructions"


def pins_for(spec: Any) -> Any:
    """``current`` → the checkout's pins; ``test`` → offline fakes; a mapping → those fields over the current pins."""
    from advisor_intel.evals.extraction_eval import current_version_pins
    from advisor_intel.models.runs import VersionPins

    if spec in (None, "", "current"):
        return current_version_pins()
    if spec == "test":
        return VersionPins()
    if isinstance(spec, dict):
        return current_version_pins().model_copy(update=spec)
    raise ValueError(f"unknown pins {spec!r}: use current, test or a mapping of VersionPins fields")


def scenario_for(prompt: str, test_vars: dict[str, Any]) -> Any:
    """The named scenario, or the base scenario with the prompt spliced in as an injected client utterance."""
    from advisor_intel.evals import redteam as RT

    name = str(test_vars.get("scenario") or prompt).strip()
    try:
        return RT.scenario(name)
    except KeyError:
        base = RT.scenario(str(test_vars.get("base") or DEFAULT_BASE))
        return base.with_utterance(name, speaker=str(test_vars.get("speaker") or "client_primary"))


def call_api(prompt: str, options: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from advisor_intel.evals import redteam as RT

    config = (options or {}).get("config") or {}
    test_vars = (context or {}).get("vars") or {}
    try:
        s = scenario_for(prompt, test_vars)
        outcome = asyncio.run(
            RT.run_scenario(s, pins=pins_for(config.get("pins")), model=config.get("model"), trace=bool(config.get("trace", True)))
        )
    except Exception as e:  # noqa: BLE001 - promptfoo reports a provider error per test
        return {"error": f"{type(e).__name__}: {e}"}
    return {
        "output": outcome.model_dump_json(),
        "metadata": {"scenario": outcome.scenario, "kind": outcome.kind, "criterion": outcome.criterion.summary()},
    }


if __name__ == "__main__":  # a quick local check: python provider.py hedges [test|current]
    import json

    result = call_api(
        sys.argv[1] if len(sys.argv) > 1 else "hedges", {"config": {"pins": sys.argv[2] if len(sys.argv) > 2 else "test"}}, {}
    )
    print(json.dumps(result, indent=2)[:4000])
