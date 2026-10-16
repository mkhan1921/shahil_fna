"""Uploads the online scorers (spec §13.4, §14.4) to the Braintrust project that receives production traces:

    BRAINTRUST_API_KEY=… braintrust push advisor_intel/evals/online_push.py

The project's online scoring rules then run them on every production trace; alerts are set on their scores.
"""

from advisor_intel.evals.online import register_online_scorers

SCORERS = register_online_scorers()
