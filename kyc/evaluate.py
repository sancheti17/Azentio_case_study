"""Compatibility wrapper for the canonical :mod:`kyc.eval` evaluator."""
from pathlib import Path
from .config import DEFAULT_REVIEW_THRESHOLD
from .eval import main, run_evaluation

def evaluate(data: str | Path, threshold: float | None = None) -> dict:
    value=DEFAULT_REVIEW_THRESHOLD if threshold is None else float(threshold)
    return run_evaluation(data, provider="fake", threshold_candidates=(value,))

if __name__ == "__main__": main()
