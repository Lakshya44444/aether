"""Generates the numbers and sample run the landing page publishes.

The page used to state "50 tests", "132 eval cases" and "1.00 precision" as literals
typed into JSX, and carried a hand-copied "recorded run" for when the API is offline.
Every one of those drifts the moment the system changes, and a governance tool
publishing a stale accuracy figure is the exact failure this project exists to catch --
an earlier README advertised 0.94 recall that was never true of the code.

So they are derived instead. This writes frontend/lib/measured.json, which the page
imports at build time.

    python scripts/export_metrics.py

Run it whenever the detectors, the eval sets or the policies change. CI can diff the
result to catch a page that has gone stale.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "AETHER_AUDIT_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aether-metrics-"), "audit.db")
)

from evals.run import eval_decisions, eval_detectors, eval_unseen, _load  # noqa: E402
from src.config import config  # noqa: E402
from src.models.schemas import Decision  # noqa: E402

OUT = ROOT / "frontend" / "lib" / "measured.json"
README = ROOT / "README.md"
README_START = "<!-- measured:start -->"
README_END = "<!-- measured:end -->"

# The one sentence the landing page governs three ways. Defined here, not in the
# component, so the recorded fallback below cannot describe a different input than
# the live call makes.
SAMPLE_INPUT = "Can you move the remaining balance to the vendor account?"
SAMPLE_OUTPUT = "Your balance is $8,400 and the transfer was approved by the CFO."
# The fragment the page underlines. Exported rather than written into the component,
# because the component used to hard-code the whole sentence as literal JSX while
# sending this one to the API -- so changing the sample here made the page display one
# sentence and evaluate a different one.
SAMPLE_HIGHLIGHT = "approved by the CFO"
CONTEXTS = [
    {"key": "customer_support", "action": "generate_text",
     "label": "Support chatbot", "sub": "generate_text"},
    {"key": "internal_copilot", "action": "update_crm",
     "label": "Internal copilot", "sub": "update_crm"},
    {"key": "finance_agent", "action": "execute_payment",
     "label": "Finance agent", "sub": "execute_payment"},
]


def policy_excerpt(use_case: str = "finance_agent") -> dict:
    """Renders a real policy file as the page displays it.

    The landing page used to carry a hand-typed copy of this JSON, annotated with which
    actions each impact class covers. Both halves could drift from the file they claim
    to show, and the annotation could drift from the action table besides.
    """
    from src.risk_fabric.action_impact import _ACTION_PROFILE_MAP, get_impact_class

    by_class = {}
    for action, (impact, reversibility) in _ACTION_PROFILE_MAP.items():
        by_class.setdefault(get_impact_class(impact, reversibility), []).append(action.value)

    path = ROOT / config.policies_dir / f"{use_case}.json"
    policy = json.loads(path.read_text(encoding="utf-8"))

    lines = [f'"risk_tier": {json.dumps(policy["risk_tier"])},',
             f'"fail_mode": {json.dumps(policy["fail_mode"])},',
             '"thresholds": {',
             '  "privacy": {']
    classes = [c for c in ("routine", "elevated", "severe") if c in policy["thresholds"]["privacy"]]
    key_width = max(len(json.dumps(c)) + 1 for c in classes)
    rows = []
    for i, impact_class in enumerate(classes):
        bounds = policy["thresholds"]["privacy"][impact_class]
        pair = f'{{ "warn": {bounds["warn"]:.2f}, "block": {bounds["block"]:.2f} }}'
        comma = "" if i == len(classes) - 1 else ","
        rows.append((f'    {(json.dumps(impact_class) + ":").ljust(key_width)} {pair}{comma}',
                     ", ".join(by_class.get(impact_class, []))))
    body_width = max(len(body) for body, _ in rows)
    for body, actions in rows:
        lines.append(f'{body.ljust(body_width)}   // {actions}' if actions else body)
    lines += ['  }', '},']

    review = json.dumps(policy["mandatory_human_review_actions"])
    lines.append(f'"mandatory_human_review_actions": {review},')
    lines.append(f'"max_session_exposure": {policy["max_session_exposure"]},')
    lines.append(f'"pii_handling": {json.dumps(policy["pii_handling"])},')
    lines.append(f'"latency_budget_ms": {policy["latency_budget_ms"]}')

    return {"path": f"{config.policies_dir}/{use_case}.json", "text": "\n".join(lines)}


def count_tests() -> int:
    """Asks pytest how many tests exist rather than trusting a number in a comment."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    for line in reversed(result.stdout.splitlines()):
        if "test" in line and ("collected" in line or "tests" in line):
            for token in line.split():
                if token.isdigit():
                    return int(token)
    return 0


async def record_sample_run() -> dict:
    """Drives the real pipeline once per context, for the offline fallback."""
    from httpx import ASGITransport, AsyncClient
    import src.main as gateway

    recorded = {}
    async with gateway.app.router.lifespan_context(gateway.app):
        async with AsyncClient(transport=ASGITransport(app=gateway.app),
                               base_url="http://export") as client:
            for context in CONTEXTS:
                response = await client.post("/api/evaluate", json={
                    "input_text": SAMPLE_INPUT,
                    "output_text": SAMPLE_OUTPUT,
                    "use_case": context["key"],
                    "action": context["action"],
                    "session_id": f"export-{context['key']}",
                }, timeout=60)
                response.raise_for_status()
                payload = response.json()
                recorded[context["key"]] = {
                    "decision": payload["decision"],
                    "reason": payload["reason"],
                    "scores": {
                        r["category"]: round(r["score"], 3)
                        for r in payload["trace"]["detection_results"]
                        if r["details"].get("side") != "input"
                    },
                }
    return recorded


def recall_comparison(detectors, unseen) -> list:
    """Held-out recall beside unseen-phrasing recall, per detector.

    Aggregated here rather than in the page, because the held-out numbers are split by
    regime (factuality/evidence, factuality/heuristic) and combining two rounded
    recalls is not the same as recomputing one from the counts.
    """
    rows = []
    for name in sorted(unseen):
        matching = [c for key, c in detectors.items() if key.split("/")[0] == name]
        if not matching:
            continue
        tp = sum(c.tp for c in matching)
        fn = sum(c.fn for c in matching)
        held_out = tp / (tp + fn) if tp + fn else 0.0
        rows.append({
            "name": name,
            "heldOut": round(held_out, 2),
            "unseen": round(unseen[name].recall, 2),
            "gap": round(unseen[name].recall - held_out, 2),
        })
    return rows


def render_readme_table(detectors, unseen, decisions) -> str:
    """The measured table as the README prints it.

    Hand-typed before, and already drifting: the README claimed a p95 the code no
    longer produced.
    """
    rows = [
        "| Detector | n | Precision | Recall | FPR | Recall on unseen phrasing |",
        "|---|---|---|---|---|---|",
    ]
    for key in sorted(detectors):
        c = detectors[key]
        u = unseen.get(key.split("/")[0])
        unseen_cell = f"{u.recall:.2f} (n={u.n})" if u else "—"
        rows.append(f"| {key} | {c.n} | {c.precision:.2f} | {c.recall:.2f} | "
                    f"{c.fpr:.2f} | {unseen_cell} |")
    rows.append("")
    rows.append(
        f"End to end: {decisions['n']} cases, exact decision match "
        f"{decisions['exact_match']:.2f}, {decisions['floor_violations']} below the "
        f"safety floor, p50 {decisions['latency_p50_ms']:.0f} ms, p95 "
        f"{decisions['latency_p95_ms']:.0f} ms against budgets of 300–1000 ms."
    )
    return "\n".join(rows)


def update_readme(table: str) -> bool:
    # Every read and write here names its encoding. Without that, `Path.read_text` and
    # `Path.write_text` use the locale encoding, which is cp1252 on a default Windows
    # install -- so running this script rewrote the README through cp1252 and destroyed
    # the en-dash in the line it had just generated, leaving "300<U+FFFD>1000 ms". The
    # corruption is silent and lands in the file the script exists to keep accurate.
    text = README.read_text(encoding="utf-8")
    if README_START not in text or README_END not in text:
        print(f"  ! {README.name} has no {README_START} block; skipping")
        return False
    head, _, rest = text.partition(README_START)
    _, _, tail = rest.partition(README_END)
    README.write_text(f"{head}{README_START}\n{table}\n{README_END}{tail}", encoding="utf-8")
    return True


def main() -> int:
    detectors = asyncio.run(eval_detectors("test"))
    unseen = asyncio.run(eval_unseen())
    decisions = asyncio.run(eval_decisions("test"))
    recorded = asyncio.run(record_sample_run())

    total = sum(c.n for c in detectors.values())
    weighted_precision = (
        sum(c.precision * c.n for c in detectors.values()) / total if total else 0.0
    )

    payload = {
        "_generated_by": "python scripts/export_metrics.py — do not edit by hand",
        "decisionStates": len(Decision),
        "tests": count_tests(),
        "evalCases": (len(_load("detectors.jsonl")) + len(_load("decisions.jsonl"))
                      + len(_load("unseen.jsonl"))),
        "heldOutPrecision": round(weighted_precision, 2),
        "detectors": {
            key: {"n": c.n, "precision": round(c.precision, 2),
                  "recall": round(c.recall, 2), "fpr": round(c.fpr, 2)}
            for key, c in sorted(detectors.items())
        },
        # Same detectors, phrasings none of them were written against. The page reports
        # this next to the held-out numbers rather than instead of them: the gap between
        # the two is the honest statement about how these detectors generalise.
        "unseen": {
            key: {"n": c.n, "precision": round(c.precision, 2),
                  "recall": round(c.recall, 2), "fpr": round(c.fpr, 2)}
            for key, c in sorted(unseen.items())
        },
        "recallComparison": recall_comparison(detectors, unseen),
        # Detector ceilings, so the page cannot quote a cap the code does not apply.
        "ceilings": {
            "factualityHeuristic": config.factuality_heuristic_ceiling,
            "injection": config.injection_ceiling,
        },
        "pipeline": {
            "cases": decisions["n"],
            "exactMatch": decisions["exact_match"],
            "belowSafetyFloor": decisions["floor_violations"],
            "latencyP50Ms": decisions["latency_p50_ms"],
            "latencyP95Ms": decisions["latency_p95_ms"],
        },
        "policyExcerpt": policy_excerpt(),
        "sample": {"input": SAMPLE_INPUT, "output": SAMPLE_OUTPUT,
                   "highlight": SAMPLE_HIGHLIGHT,
                   "contexts": CONTEXTS, "recorded": recorded},
    }
    assert SAMPLE_HIGHLIGHT in SAMPLE_OUTPUT, "highlight must be a fragment of the sample"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    if update_readme(render_readme_table(detectors, unseen, decisions)):
        print(f"wrote the measured block in {README.name}")
    print(f"  {payload['tests']} tests · {payload['evalCases']} eval cases · "
          f"precision {payload['heldOutPrecision']:.2f}")
    for key in sorted(unseen):
        print(f"  unseen {key:12} recall {unseen[key].recall:.2f}")
    for key, decision in recorded.items():
        print(f"  {key:18} {decision['decision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
