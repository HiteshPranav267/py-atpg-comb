"""Unattended batch running of the ATPG/ranking pipelines.

Built for the "drop in a pile of circuits, come back later for a results
table" workflow: retries transient failures (LLM 503s, a busy Atalanta
binary) with backoff, never retries failures that are actually about the
circuit (a bad netlist), and keeps a resumable manifest so a crash or a
scheduled re-run picks up only what is still pending.
"""
from .retry import Classification, RetryOutcome, RetryPolicy, classify_error, run_with_retries
from .manifest import Manifest, ManifestRecord, fingerprint_file
from .suite import discover_circuits, run_atpg_suite, run_ranking_suite

__all__ = [
    "Classification",
    "RetryOutcome",
    "RetryPolicy",
    "classify_error",
    "run_with_retries",
    "Manifest",
    "ManifestRecord",
    "fingerprint_file",
    "discover_circuits",
    "run_atpg_suite",
    "run_ranking_suite",
]
