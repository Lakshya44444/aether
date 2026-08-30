"""Runs the eval suite as a test so a detector regression fails CI, not just review.

The thresholds live in evals/gates.json and are checked against the held-out split.
Run `python -m evals.run -v` for the full report and the per-case misses.
"""
import asyncio
import json
import pathlib

from evals.run import HERE, check_gates, eval_decisions, eval_detectors, eval_unseen


def test_held_out_split_meets_every_gate():
    """Held-out gates *and* the unseen-phrasing floors.

    `check_gates` skips the unseen bounds when it is not handed unseen results, and
    this test used to call it with three arguments -- so the floors that measure
    whether the detectors generalise were enforced by `evals.run` and `verify.py` but
    never by pytest, and a generalisation regression passed CI.
    """
    gates = json.loads((HERE / "gates.json").read_text(encoding="utf-8"))
    detectors = asyncio.run(eval_detectors("test"))
    decisions = asyncio.run(eval_decisions("test"))
    unseen = asyncio.run(eval_unseen())
    failures = check_gates(detectors, decisions, gates, unseen)
    assert not failures, "eval gates not met:\n  " + "\n  ".join(failures)


def test_dev_and_test_splits_do_not_share_cases():
    """A case in both splits would let tuning leak into the reported number."""
    rows = [json.loads(l) for l in
            (HERE / "datasets" / "detectors.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    by_split = {}
    for row in rows:
        by_split.setdefault(row["split"], set()).add(row["text"])
    assert not (by_split["dev"] & by_split["test"])
