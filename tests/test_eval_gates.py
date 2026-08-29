"""Runs the eval suite as a test so a detector regression fails CI, not just review.

The thresholds live in evals/gates.json and are checked against the held-out split.
Run `python -m evals.run -v` for the full report and the per-case misses.
"""
import asyncio
import json
import pathlib

from evals.run import HERE, check_gates, eval_decisions, eval_detectors


def test_held_out_split_meets_every_gate():
    gates = json.loads((HERE / "gates.json").read_text())
    detectors = asyncio.run(eval_detectors("test"))
    decisions = asyncio.run(eval_decisions("test"))
    failures = check_gates(detectors, decisions, gates)
    assert not failures, "eval gates not met:\n  " + "\n  ".join(failures)


def test_dev_and_test_splits_do_not_share_cases():
    """A case in both splits would let tuning leak into the reported number."""
    rows = [json.loads(l) for l in
            (HERE / "datasets" / "detectors.jsonl").read_text().splitlines() if l.strip()]
    by_split = {}
    for row in rows:
        by_split.setdefault(row["split"], set()).add(row["text"])
    assert not (by_split["dev"] & by_split["test"])
