"""The extraction experiment as a ``braintrust eval`` file (spec §15.4); the CI eval gate and the nightly gold suite
run it:

    BRAINTRUST_API_KEY=… braintrust eval advisor_intel/evals/eval_extraction.py

``Eval("advisor-extraction", data=init_dataset("advisor-extraction", <dataset>), task=run_stages_3_to_6_from_cache,
scores=[the per-item §15.3 scorers], metadata=current_version_pins())``; the reporter adds the summary scores and
writes the JSON report the gate step reads. Stage 1–2 outputs come from the cache, built on a miss (synthetic
scripts through stage 1's back half, gold calls from ``gold-transcripts``, then stage 2).

Environment: ``ADVISOR_INTEL_EVAL_DATASET`` (``synthetic-calls``, the default, or ``gold-calls``),
``ADVISOR_INTEL_EVAL_EXPERIMENT`` (an experiment name), ``ADVISOR_INTEL_EVAL_GATE=1`` to gate inside the reporter,
``ADVISOR_INTEL_EVAL_BASELINE`` (a report path or ``store:<ref>``). Without ``BRAINTRUST_API_KEY`` it runs on the
local datasets and sends nothing.
"""

from __future__ import annotations

import os

from advisor_intel.evals.datasets import stage12_builder
from advisor_intel.evals.extraction_eval import braintrust_eval
from advisor_intel.evals.synthetic import SYNTHETIC_DATASET

DATASET = os.environ.get("ADVISOR_INTEL_EVAL_DATASET") or SYNTHETIC_DATASET

braintrust_eval(dataset=DATASET, experiment_name=os.environ.get("ADVISOR_INTEL_EVAL_EXPERIMENT") or None, build=stage12_builder())
