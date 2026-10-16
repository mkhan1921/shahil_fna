"""Evaluation, datasets, CI gates, prompt optimisation, red-teaming, synthetic calls, fine-tuning and RL (spec §15).

Modules:

- ``gold``: the gold-call format, annotation import, adjudication and inter-annotator agreement (§15.1–15.2).
- ``records``: what one pipeline run looks like to the scorers (``SystemCallOutput``) and how it is built.
- ``scorers``: every §15.3 metric as a plain function, plus Braintrust scorer adapters.
- ``synthetic``: ground-truth households, call scripts and gold facts (§15.1), and the synthetic-data guard.
- ``datasets``: Braintrust dataset sync (local JSONL offline), the error and gold-candidate syncs (§11.4) and the
  Stage 1–2 output cache (§15.1).
- ``extraction_eval`` / ``eval_extraction``: the Braintrust ``Eval`` and an offline runner (§15.4); ``gate`` and
  ``model_swap``: the CI gate and the model-swap protocol (§15.4).
- ``gepa_adapter``: prompt optimisation with GEPA (§15.6).
- ``rl_reward`` and ``finetune``: the fine-tuning and RL tracks (§15.8–15.9).
- ``error_analysis``: weekly error clustering and sampling (§15.5).
- ``online``: online scorers over production traces (§13.4, §14.4).
- ``redteam``: scenario and injection transcripts and the pass criterion (§15.7).

Nothing here imports another stage's package at module import time: the pipeline runner, ingestion, the FNA engine
and the forecast are reached through injected callables or lazy imports, so this package works offline.
"""
