"""Normalisation and the vagueness lexicon (spec §8).

The LLM never invents numbers for vague language: it returns a typed intermediate form (§8.1) and
deterministic code here turns that form into a distribution using a versioned lexicon (§8.2–8.3).
"""

from .loader import CURRENT_VERSION, Lexicon, Likelihood, Lookup, MissLog, MissRecord, RecencyWeights, load_lexicon

__all__ = [
    "CURRENT_VERSION",
    "Lexicon",
    "Likelihood",
    "Lookup",
    "MissLog",
    "MissRecord",
    "RecencyWeights",
    "load_lexicon",
]
