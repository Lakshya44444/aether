"""Sentinel evaluation harness.

Three questions, kept separate because they fail for different reasons:

  1. detectors  — does each detector separate risky text from safe text?
  2. decisions  — does the whole pipeline land on the right governance decision?
  3. latency    — does it do that inside the budget the policy declares?

Every case carries a `split`. `dev` is the only data tuning is allowed to look at;
`test` is held out and is what the gates in gates.json are checked against. A detector
tuned against the set it is scored on reports its own memory, not its accuracy.

    python -m evals.run                   # full report, gates on the test split
    python -m evals.run --split dev       # inspect the tuning set
    python -m evals.run --json report.json
"""
import argparse
import asyncio
import json
import os
import pathlib
import statistics
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "SENTINEL_AUDIT_DB_PATH",
    os.path.join(tempfile.mkdtemp(prefix="sentinel-evals-"), "audit.db"),
)

from src.detectors.bias import BiasDetector          # noqa: E402
from src.detectors.factuality import FactualityDetector  # noqa: E402
from src.detectors.injection import InjectionDetector    # noqa: E402
from src.detectors.privacy import PrivacyDetector        # noqa: E402
from src.models.schemas import Decision, VerificationDepth  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SEVERITY = {Decision.ALLOW: 0, Decision.WARN: 1, Decision.REDACT: 2,
            Decision.ESCALATE: 3, Decision.BLOCK: 4}


def _load(name):
    with open(HERE / "datasets" / name, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── metrics ──────────────────────────────────────────────────────────────────

class Confusion:
    """Counts and the four numbers that matter, kept in one place.

    Recall is the share of real risks caught; FPR is the share of safe text stopped
    anyway. A governance tool is judged on both at once -- a detector that flags
    everything has perfect recall and is useless.
    """

    def __init__(self):
        self.tp = self.fp = self.fn = self.tn = 0
        self.misses = []

    def add(self, label, predicted, case):
        if label and predicted:
            self.tp += 1
        elif label and not predicted:
            self.fn += 1
            self.misses.append(("missed", case))
        elif not label and predicted:
            self.fp += 1
            self.misses.append(("false alarm", case))
        else:
            self.tn += 1

    @property
    def n(self):
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self):
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self):
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def fpr(self):
        return self.fp / (self.fp + self.tn) if self.fp + self.tn else 0.0

    @property
    def f1(self):
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self):
        return {"n": self.n, "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
                "precision": round(self.precision, 3), "recall": round(self.recall, 3),
                "f1": round(self.f1, 3), "fpr": round(self.fpr, 3)}


# ── suite 1: detectors in isolation ──────────────────────────────────────────

async def eval_detectors(split):
    detectors = {
        "privacy": PrivacyDetector(),
        "bias": BiasDetector(),
        "injection": InjectionDetector(),
        "factuality": FactualityDetector(),
    }
    buckets = {}
    for case in _load("detectors.jsonl"):
        if case["split"] != split:
            continue
        category = case["category"]
        key = f"{category}/{case['regime']}" if "regime" in case else category

        if category == "injection":
            result = await detectors[category].detect(case["text"], "")
        elif category == "factuality":
            result = await detectors[category].detect(
                "What happened?", case["text"],
                depth=VerificationDepth.MEDIUM,
                context_documents=case.get("context"),
            )
        else:
            result = await detectors[category].detect("What happened?", case["text"])

        buckets.setdefault(key, Confusion()).add(
            case["label"], result.flagged,
            f"[{case['id']}] {case['note']}: {case['text'][:64]} (score {result.score:.2f})",
        )
    return buckets


# ── suite 2: the whole pipeline ──────────────────────────────────────────────

async def eval_decisions(split):
    from httpx import ASGITransport, AsyncClient
    import src.main as sentinel

    exact = floor_violations = total = 0
    latencies = []
    problems = []

    async with sentinel.app.router.lifespan_context(sentinel.app):
        async with AsyncClient(transport=ASGITransport(app=sentinel.app),
                               base_url="http://evals") as client:
            for case in _load("decisions.jsonl"):
                if case["split"] != split:
                    continue
                total += 1
                body = {k: case[k] for k in
                        ("input_text", "output_text", "use_case", "action", "context_documents")}
                # A fresh session per case: session exposure is cumulative by design, so
                # sharing one would make every result depend on the order of the file.
                body["session_id"] = f"eval-{case['id']}"
                response = await client.post("/api/evaluate", json=body, timeout=60)
                response.raise_for_status()
                payload = response.json()

                got = Decision(payload["decision"])
                latencies.append(payload["trace"]["total_latency_ms"])

                if got.value == case["expected"]:
                    exact += 1
                # The floor is the safety requirement: landing softer than this means a
                # risk was let through, which is the failure that actually matters.
                if SEVERITY[got] < SEVERITY[Decision(case["floor"])]:
                    floor_violations += 1
                    problems.append(
                        f"[{case['id']}] UNDER FLOOR: got {got.value}, "
                        f"floor {case['floor']} -- {payload['reason'][:100]}")
                elif got.value != case["expected"]:
                    problems.append(
                        f"[{case['id']}] off expected: got {got.value}, "
                        f"expected {case['expected']} (above floor {case['floor']})")

    return {
        "n": total,
        "exact_match": round(exact / total, 3) if total else 0.0,
        "floor_violations": floor_violations,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "latency_p95_ms": round(
            statistics.quantiles(latencies, n=20)[-1], 1) if len(latencies) > 1 else 0.0,
        "problems": problems,
    }


# ── reporting ────────────────────────────────────────────────────────────────

def check_gates(detectors, decisions, gates):
    failures = []
    for key, bounds in gates["detectors"].items():
        confusion = detectors.get(key)
        if confusion is None or confusion.n == 0:
            failures.append(f"{key}: no held-out cases to score")
            continue
        if "min_recall" in bounds and confusion.recall < bounds["min_recall"]:
            failures.append(
                f"{key}: recall {confusion.recall:.2f} < {bounds['min_recall']:.2f}")
        if "max_fpr" in bounds and confusion.fpr > bounds["max_fpr"]:
            failures.append(
                f"{key}: false-positive rate {confusion.fpr:.2f} > {bounds['max_fpr']:.2f}")

    if decisions["floor_violations"] > gates["decisions"]["max_floor_violations"]:
        failures.append(
            f"decisions: {decisions['floor_violations']} case(s) landed below their "
            f"safety floor (allowed {gates['decisions']['max_floor_violations']})")
    if decisions["exact_match"] < gates["decisions"]["min_exact_match"]:
        failures.append(
            f"decisions: exact match {decisions['exact_match']:.2f} < "
            f"{gates['decisions']['min_exact_match']:.2f}")
    if decisions["latency_p95_ms"] > gates["latency"]["max_p95_ms"]:
        failures.append(
            f"latency: p95 {decisions['latency_p95_ms']:.0f}ms > "
            f"{gates['latency']['max_p95_ms']}ms")
    return failures


def print_detectors(title, buckets, verbose):
    print(f"\n{title}")
    print(f"  {'detector':22} {'n':>4} {'prec':>6} {'recall':>7} {'F1':>6} {'FPR':>6}   "
          f"{'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3}")
    for key in sorted(buckets):
        c = buckets[key]
        print(f"  {key:22} {c.n:>4} {c.precision:>6.2f} {c.recall:>7.2f} "
              f"{c.f1:>6.2f} {c.fpr:>6.2f}   {c.tp:>3} {c.fp:>3} {c.fn:>3} {c.tn:>3}")
    if verbose:
        for key in sorted(buckets):
            for kind, case in buckets[key].misses:
                print(f"    {kind:12} {case}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test", "both"), default="both")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="list every miss and false alarm")
    parser.add_argument("--json", help="write the full report to this path")
    args = parser.parse_args()

    gates = json.loads((HERE / "gates.json").read_text())
    splits = ("dev", "test") if args.split == "both" else (args.split,)

    print("Sentinel evaluation")
    print("=" * 78)

    detector_results = {}
    decision_results = {}
    for split in splits:
        detector_results[split] = asyncio.run(eval_detectors(split))
        label = "tuning set" if split == "dev" else "HELD OUT — gates apply here"
        print_detectors(f"Detectors — {split} split ({label})",
                        detector_results[split], args.verbose)

        decision_results[split] = asyncio.run(eval_decisions(split))
        d = decision_results[split]
        print(f"\nEnd-to-end decisions — {split} split")
        print(f"  cases {d['n']}   exact match {d['exact_match']:.2f}   "
              f"below safety floor {d['floor_violations']}")
        print(f"  latency p50 {d['latency_p50_ms']:.0f}ms   p95 {d['latency_p95_ms']:.0f}ms")
        for problem in d["problems"]:
            print(f"    {problem}")

    gate_split = "test" if "test" in splits else splits[0]
    failures = check_gates(detector_results[gate_split], decision_results[gate_split], gates)

    print("\n" + "=" * 78)
    if failures:
        print(f"GATE FAILED on the {gate_split} split — {len(failures)} threshold(s) not met:")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print(f"All gates met on the {gate_split} split.")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({
            "detectors": {s: {k: c.as_dict() for k, c in b.items()}
                          for s, b in detector_results.items()},
            "decisions": decision_results,
            "gate_split": gate_split,
            "gate_failures": failures,
        }, indent=2))
        print(f"Report written to {args.json}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
