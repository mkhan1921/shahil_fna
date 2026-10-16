"""Stage 5 — verification, calibration and conformal filtering (spec §9).

- ``claims``: claim sentences per slot and value (§9.1a)
- ``entailment`` / ``minicheck``: the verifier agent and the grounding pre-filter (§9.1a, §9.1e)
- ``consistency``: paraphrased re-extraction probes (§9.1b)
- ``coverage``: the expected-fact list and closed omission questions (§9.1d)
- ``features``: the calibration feature vector (§9.2)
- ``labels``: calibration labels from reviewer actions (§9.3)
- ``calibrator``: isotonic, gradient boosting and Venn-Abers calibration (§9.4)
- ``conformal``: Mondrian split-conformal auto-accept and spot checks (§9.5, §11.3)
- ``run``: ``verify_facts``, ``check_coverage`` and ``score_profile`` (the tools of §13.3)

Constraint rules (§9.1c) live with the schema in ``advisor_intel.schema.constraints``.
"""
