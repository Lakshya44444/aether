# Research references

Every technique this repo borrows, the component that implements it, and — the column
that matters — how much of it is actually implemented. A citation next to a stub is a
claim about reading, not about code, so the status is stated rather than implied.

Status means:

- **Implemented** — the technique, as described, runs in the request path.
- **Implemented, needs a judge model** — the code path exists and is exercised by tests,
  but does nothing without `AETHER_LLM_API_KEY`; offline it degrades to a capped
  heuristic that is labelled as such on the result.
- **Approximated** — the shape of the method, without the part that makes it the method.
  Called out in the file's own docstring too.
- **Not implemented** — cited because it informed a decision, or because it is the
  documented upgrade path. No code claims it.

---

## Detection

| # | Source | Component | Status |
|---|---|---|---|
| 1 | Manakul et al., *SelfCheckGPT*, EMNLP 2023 | `detectors/factuality.py` → `_consistency_branch`, `detectors/judge.py` → `sample_answers` | **Implemented, needs a judge model.** Samples *n* independent answers to the same question and scores disagreement. Offline this branch is not reached. |
| 2 | Es et al., *Ragas*, arXiv:2309.15217 | `detectors/factuality.py` → `_evidence_branch` | **Implemented.** Claim decomposition plus context verification; score is `unsupported / total_claims`, Ragas' coverage formula. Novel figures and names outrank the overlap score rather than averaging into it. |
| 3 | Farquhar et al., *Semantic entropy*, Nature 630 (2024) | — | **Not implemented.** Motivates meaning-level rather than string-level consistency; the shipped consistency branch is the cheaper SelfCheckGPT variant and does not compute semantic entropy. |
| 4 | Desai et al., *SafeGPT*, arXiv:2601.06366 | `input_guardrail/guardrail.py`, and the two input-side detectors in `main.py` | **Implemented.** The two-sided premise: the prompt is screened as well as the completion. Input findings carry `side: "input"` because their offsets index the prompt and must not reach the output redaction path. |
| 5 | Microsoft Presidio | — | **Not implemented.** The documented upgrade path for NER-based unstructured PII. `detectors/privacy.py` is pattern plus checksum only (Luhn, ISO 7064 mod-97), which is why the README reports its recall rather than asserting coverage. |

## Routing and cost

| # | Source | Component | Status |
|---|---|---|---|
| 6 | Ong et al., *RouteLLM*, ICLR 2025 | `verification_router/router.py` | **Implemented**, with the pattern repurposed: it routes between shallow, medium and deep *verification* rather than between a strong and a weak model. No trained router — the decision is a lookup on tier and impact class, both known before detection runs, so routing costs nothing. |
| 7 | Production guardrail latency benchmarks (Galileo Luna-2, Lakera Guard, Patronus Glider) | `config.py` → `shallow/medium/deep_latency_budget_ms` = 200 / 700 / 1000 ms | **Implemented as budgets.** The shallow budget is set to the Luna-2 class and the deep budget to the Glider class. Enforced per policy via `asyncio.wait_for`; a detector that overruns is treated as having produced no signal and takes the policy's `fail_mode`. |
| 8 | Regmi & Pun, *GPT Semantic Cache*, arXiv:2411.05276 | — | **Not implemented.** Future work. Nothing in this repo caches a risk assessment. |

## Correction

| # | Source | Component | Status |
|---|---|---|---|
| 9 | Dhuliawala et al., *Chain-of-Verification*, arXiv:2309.11495 | `correction/cove_revise.py` | **Approximated.** Marks flagged claims as unverified; it does not plan verification questions or answer them independently. It cannot smuggle anything through: `main.py` re-runs the detectors and the policy on the rewrite and keeps it only if the second pass genuinely lands softer. |
| 10 | Cheng et al., *BiasFilter*, arXiv:2505.23829 | `correction/bias_resample.py` | **Approximated.** Substitutes a neutral placeholder; there is no trained reward model and no resample-and-score. Same re-verification gate applies. |

## Governance framing

| # | Source | Component | Status |
|---|---|---|---|
| 11 | EU AI Act (Regulation 2024/1689) | `models/schemas.py` → `RiskTier` | **Implemented as taxonomy.** The enum is the Act's own four tiers — `minimal`, `limited`, `high`, `unacceptable` — rather than a bespoke naming, so a policy's `risk_tier` is readable against the regulation. Aether does not implement conformity assessment; it borrows the vocabulary. |
| 12 | Su et al., *API Is Enough*, Findings of EMNLP 2024; Mohri & Hashimoto (2024) | — | **Not implemented.** The documented Version 2 path for turning session exposure into something with coverage guarantees. Until then `session_exposure` is labelled a governance heuristic in `risk_fabric/session_tracker.py` and is not presented as a calibrated probability. |
| 13 | SOC alert-fatigue benchmarks (FPR under 10%, alert-to-incident above 20%) | `evals/gates.json`, `GET /api/stats` → `alert_to_incident_rate` | **Implemented as measurement.** The gates hold measured FPR at or below the recorded value, and the stats endpoint computes alert-to-incident conversion from chained human-review verdicts rather than from a configured constant. |

---

## What the numbers in the README are

Measured by `python -m evals.run` on a held-out split, and by a separate
unseen-phrasing set. Both are small, both were written by this project, and the README
says so. `scripts/export_metrics.py` regenerates every published figure by running the
real pipeline, so no accuracy claim in this repo is typed by hand.

The honest summary of the gap between the two sets, and each remaining miss with why a
pattern cannot reach it, is in `evals/gates.json`.
