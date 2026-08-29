# Sentinel — Audit & Fix Handoff

Session handoff covering a full audit of `Sentinel_Final_Detailed_Report (6).docx` against the
Round 2 problem statement, an evidence-based audit of the code, and 17 fix commits.

Everything described here is **local only. Nothing has been pushed.**

---

## 1. Project context

**Sentinel** — an AI runtime control plane submitted for the **Accenture Innovation Challenge 2026,
Round 2, Problem Track 1: ControlPlane.ai**. It sits between enterprise AI systems and users,
intercepts prompt and response, scores across factuality / privacy / bias / cost, contextualises
against use case + intended action + session history, and routes through a policy engine emitting
one of five decisions: `ALLOW / WARN / REDACT / ESCALATE / BLOCK`.

- **Repo:** `/home/akronim26/Desktop/Skills/Blockchain/Projects/aether`
- **Remote:** `git@github.com:Lakshya44444/aether` (shared team repo — not the user's personal repo)
- **Design doc:** `~/Downloads/Sentinel_Final_Detailed_Report (6).docx` (~6,200 words, 13 sections)

### Problem statement requirements (from the Round 2 brief)

Seven stated real-world complexities: per-use-case risk/latency budgets; overlapping bias /
hallucination / privacy; no real-time ground truth; the over- vs under-flagging tradeoff; multi-turn
and agentic compounding risk; regulation varying by geography and industry; API-only model access
(no internals).

Six solutioning areas offered: detection techniques; decision logic; architecture/placement;
governance (configurable policy + audit trail); feedback loops; metrics & monitoring.

Reference scale: multiple concurrent use cases, **tens of thousands of interactions per week**, a mix
of well- and loosely-governed data sources.

---

## 2. Git state

`main` is **ahead 22, behind 1** of `origin/main`. Publishing requires
`git push --force-with-lease` — **the user explicitly chose local-only; do not push without asking.**

### History rewrite (done first, at user request)

The repo was a single commit `1ed15ce "Initial project setup"` authored by
`Lakshya <lakshya@example.com>`. Split into 5 atomic commits.

User's decisions, both explicitly chosen — **honour these**:
- **Local rewrite only, no push.**
- **`Lakshya` preserved as author** on all 5 scaffold commits (original author + date). Committer is
  Abhivansh. Fix commits are authored by Abhivansh.

Verified byte-identical: tree hash `4a0114fa6d261bb3b2be50252a2df840ce0326d6` on both sides,
40 files both sides, `git diff backup-before-rewrite HEAD` empty at the split point.

**Safety net:** tag `backup-before-rewrite` → original `1ed15ce`. Do not delete until pushed.

### Commit style (matched from the user's `hermes` repo — solo-authored, best reference)

Conventional prefixes (`feat:` `fix:` `chore:` `test:` `doc:`), lowercase, imperative,
**subject only — no body**, **no `Co-Authored-By` trailer, no session link.** The user asked for
this explicitly. Occasionally scoped (`feat(recovery):`). Some trivial commits have no prefix.

### Full log (oldest → newest)

```
 1  0de92f3  Lakshya    chore: add project scaffold, dependencies and docs
 2  8ab7b5b  Lakshya    feat: add detection layer for factuality, privacy, bias and cost
 3  343db7e  Lakshya    feat: add risk fabric, policy engine and correction layer
 4  9be978c  Lakshya    feat: add gateway api, input guardrail, router and audit log
 5  e43b5a5  Lakshya    feat: add dashboard, demo runner and scenario tests
 6  c052598  Abhivansh  fix: apply policy fail mode when a detector raises
 7  2ba9a3e  Abhivansh  fix: index policy thresholds by action impact class
 8  45fb0fe  Abhivansh  fix: set finance agent risk tier to high
 9  767eb1a  Abhivansh  fix: mask flagged spans on a redact decision
10  784be97  Abhivansh  fix: scope cost accounting to the session and bound retry tracking
11  3f9e9e1  Abhivansh  fix: score privacy by severity and tighten pii patterns
12  35dedf4  Abhivansh  fix: correct bias patterns for apostrophes, targets and negation
13  f0be3e3  Abhivansh  feat: add judge model client for independent verification
14  157d351  Abhivansh  fix: rework factuality scoring and wire verification depth
15  0ea2cab  Abhivansh  fix: make the shallow verification path reachable
16  d4455cc  Abhivansh  fix: enforce latency budget and evict stale session state
17  3628c99  Abhivansh  fix: hash chain audit rows and correct review metrics
18  23cca80  Abhivansh  fix: re-verify corrections before accepting them
19  d7ed606  Abhivansh  chore: wire unused configuration fields to their call sites
20  0ce9a91  Abhivansh  test: add regression tests for the audited defects
21  5ea203a  Abhivansh  chore: replace deprecated utcnow and startup event handlers
22  47cb65c  Abhivansh  fix: update demo import after removing the risk multiplier
```

---

## 3. Document audit — findings NOT yet acted on

The doc was reviewed before the code. **These are all still open** — the doc has not been edited.

### The systemic issue

> The report describes a *designed* system; the repo contained a *simulated* one, and §8.1
> "Known Limitations" did not disclose the difference.

This is severe precisely because the doc stakes its credibility on honesty. **Fix §8.1 first.**
After the code fixes below, the specific disclosures needed are narrower but still required:

- No LLM is called unless `SENTINEL_LLM_API_KEY` is set and `demo_mode=false`. Offline, the
  factuality detector uses a capped heuristic, and CoVe / BiasFilter correction are `asyncio.sleep`
  stubs.
- The evidence branch is whole-word overlap, not Ragas claim decomposition + NLI.
- References #1 (Semantic Entropy) and #8 (BiasFilter) remain design lineage, not implementation.

### Other open document issues

| # | Issue |
|---|---|
| D1 | Reference 17 "SOC alert-fatigue industry benchmarks (2026)" has **no author, title or publisher** and is load-bearing (§4 #12, §6.4, §7.2). Refs 15/16 (galileo.ai, braintrust.dev) are vendor marketing cited as benchmarks. |
| D2 | **Verify every 2026 arXiv ID resolves** — SafeGPT `2601.06366`, SentGuard `2606.02041`. Five precise SafeGPT figures are quoted (92/87/<12%, 40.5%, 78.6%, 55-point drop, 34%/month). One misattribution collapses the honesty positioning. |
| D3 | Refs 13 (ICML 2025 Inspector pattern) and 14 (Reid et al.) appear **nowhere in the body**. List has 20 entries; §4 numbers 1–15. |
| D4 | §3.2's "No comparable open-source guardrail toolkit makes the action's consequence a first-class input" is too absolute — LangGraph HITL interrupts, OpenAI tool approval, Bedrock Agents confirmation all gate on action consequence. **Narrower defensible claim:** those gate *whether* approval is needed; Sentinel makes consequence modulate the *detection threshold itself*. Commit `2ba9a3e` makes that literally true in code. |
| C1 | **Largest scoring gap.** §7.2 is a table of *other people's* benchmarks with "report your actual number" in every cell. Zero Sentinel numbers. The brief explicitly asks how you'd report FP/FN "to a skeptical stakeholder". Measured numbers now exist — see §6 below. Put them in. |
| C2 | No geography/jurisdiction axis. Brief names it twice. Policies vary only by use case. ~15 lines to add a `jurisdiction` field + override map. |
| E1 | **Biggest untaken opportunity.** No claim-provenance / taint tracking. The brief's "one questionable output can shape several downstream decisions" is addressed only rhetorically, and §9's demo narrative (same figure flowing chatbot → copilot → finance agent) has no mechanism behind it — those are three different sessions. ~40 lines. Stronger originality claim than Action Impact (which has prior art per D4). |
| F1 | Section numbering: `5.2.3b` (Cost) is a peer of `5.2.3` (Bias), not a child. Same for `5.3b`. The `b` suffixes visibly mark revision bolt-ons. §5.2.2 references "Section 5.2.3-adjacent", not a real cross-ref. |
| F2 | **Architecture diagram flow is wrong.** Shows `Input Guardrail → Model → Request Context → Router`. Context must be extracted *first* or the guardrail has no tier. `main.py` already does it correctly. Redraw. |
| F3 | **Open question for the user.** §6.2 (build order), §9 ("say this out loud"), §11 (judge Q&A), §13 ("say verbatim") are internal prep. If this doc *is* the submission, they read oddly to an evaluator. Needs a decision: internal bible vs. submission doc. |
| F4 | No diagram of the 5-state decision ladder — the actual differentiator has no picture. |
| F5 | §2.1 frames the ask around Round 1's performance/cost/responsibility. **The Round 2 Track 1 brief does not mention cost at all.** Keep the cost detector (cheap, earns the "ControlPlane" name) but frame it as *continuity from Round 1*, not a Round 2 requirement. |

---

## 4. Code audit — how the defects were proven

Method: no assertions without execution. A venv was built from `requirements.txt`, and each
hypothesis was run as a script. **Two of my initial hypotheses were wrong and were corrected by the
evidence** — noted below so a future session doesn't reinstate them.

| # | Defect | Evidence |
|---|---|---|
| P1 | `adjusted_score = score * multiplier` with score ∈ [0,1], multiplier ∈ [1.0,2.5]. Policies contained `block: 1.2` / `1.7` — **unreachable**. `internal_copilot` could never BLOCK on factuality for generate_text/draft_email/send_email/update_crm. On `finance_agent + execute_payment` the *minimum flaggable score* of all four detectors already exceeded block → 5 states collapsed to 2. | Computed max reachable adjusted score per policy × action. Empirically across 26 runs: `ESCALATE: 0`. |
| P2 | `main.py` gathered with `return_exceptions=True` then filtered exceptions out. All four detectors raising on `internal_copilot` (fail_closed, generate_text) → **`ALLOW`, "All checks passed"**, empty detection list, and the trace asserted `fail_mode: fail_closed`. A falsified audit record. | Monkeypatched detectors to raise. |
| P3 | Factuality measured **surface form, not truth**. `"I can certainly help you with that."` → 1.00. All six real hallucinations → 0.00. Evidence branch used `if w in context_text` (no word boundary) so `"account"` was SUPPORTED by `"accountant"`. | 38-case labeled set. |
| P4 | Privacy scored `min(1.0, count * 0.3)` — quantity, not harm. One SSN 0.30; four internal IPs 1.00. `FlaggedSpan.severity` computed then never read. **Unpredicted bonus bug:** ADDRESS regex alternated bare `St` under IGNORECASE with no left boundary → `"3 nodes last night"`, `"5 tests must pass"`, `"Order 42 will cost less"` all matched as street addresses. | Direct regex probes. |
| P5 | `women’s work` hardcoded U+2019 → **unmatchable against typed input**. `age_bias` had no subject check → `"This milk is too old"` flagged. | Both apostrophe forms tested. |
| P6 | **Measured: precision 0.43 / recall 0.56 / FPR 0.65.** Factuality alone: 0.00 / 0.00. | 38 hand-labeled cases. |
| P7 | Headline claim vs NeMo ("we run checks in parallel") was **structurally false**: all four detectors had **zero await points**, so `asyncio.gather` ran them strictly serially (measured 201ms for 4×50ms vs 51ms with real awaits). | AST walk + timing. |
| P8 | `cost.py` used a linear list scan for retries (O(n²)) and `main.py:78` passed **no `session_id`** → one global `default_session` bucket. A brand-new tenant inherited all prior spend. Per-call cost 0.027ms → 0.41ms at 20k history. `SessionTracker` never evicted (`session_timeout_minutes` never read). | Scale run to 20k. |
| P9 | `"""Insert immutable row."""` — no hash chain, no trigger; `log_human_review()` `UPDATE`s the table itself. `false_negative_count` permanently 0 (`else: pass`). `alert_to_incident_rate` computed `(BLOCK+ESCALATE)/total` — that's the **alert rate**, published under the cited benchmark's name. | UPDATE + DELETE against the live DB. |
| P10 | `engine.py:84` guarded on `(ALLOW, WARN)`, `:94` on `{ESCALATE, WARN}` — **REDACT in neither**. One threshold edit (which the doc invites) → irreversible `delete_record` bypassed both safety upgrades with PII intact. Also `if _SEVERITY[BLOCK] >= _SEVERITY[worst]` was always true (dead conditional). | Runtime threshold mutation. |
| P11 | **14 of 21 config fields dead**, including `demo_mode` (so there *was* no non-demo mode), all three latency budgets, all three thresholds. `depth` was computed, passed to the detector, and **read by nothing**. `SHALLOW` required tier `MINIMAL` which no policy declared — the `<200ms` path was unreachable. | grep audit + router enumeration. |

### Corrections to my own initial hypotheses (do not reinstate)

- **"The exposure rule is dead."** Wrong — it fires from turn 2. The real problem was that factuality
  saturated at 1.00 on benign text, so exposure hit 1.00 regardless of actual risk.
- **"fail_closed always allows."** Partially wrong — on `finance_agent` it returned BLOCK, but from
  the *mandatory-review + irreversible-action* rules, not from fail-closed. The genuine failure is
  `internal_copilot + generate_text`, where no such rule rescues it.

---

## 5. What was fixed

| Commit | Change |
|---|---|
| `c052598` | Apply `fail_mode` when any detector raises; record failures in `DecisionTrace.failed_detectors` (new field). |
| `2ba9a3e` | **Removed `compute_action_risk_multiplier` entirely.** Added `get_impact_class()` → `routine` / `elevated` / `severe`. Policy thresholds are now nested `category → impact_class → {warn, block}`, all within [0,1]. Engine also now evaluates **every** result by score, not just ones a detector chose to `flag` — `flagged` is a detector-local opinion; policy decides. REDACT added to both safety-upgrade guards; dead conditional removed. |
| `45fb0fe` | `finance_agent.risk_tier`: `unacceptable` → `high`. Under AI Act Art. 5 "unacceptable" means **prohibited outright**. |
| `767eb1a` | New `src/correction/redact.py`. Masks spans right-to-left; collapses overlapping spans so masks can't nest. |
| `784be97` | `session_id` + `model_name` passed through; `Counter` instead of list scan (O(1)); `MAX_TRACKED_PROMPTS = 512`; `retry_count` now feeds a real score (was dead). |
| `3f9e9e1` | Score = `max(span.severity)` + 0.1 per extra distinct type. Per-type severity map. `ipaddress` validation, private/loopback/reserved ranges excluded, version-context lookbehind. ADDRESS suffixes anchored as whole tokens. |
| `35dedf4` | `['’]` character class for apostrophes; `_PERSON` target required for age terms; `_NEGATION` lookbehind so discussing bias isn't bias. |
| `f0be3e3` | New `src/detectors/judge.py` — OpenAI-compatible client, `sample_answers()` (SelfCheckGPT) and `entails()`. Activated by `SENTINEL_LLM_API_KEY` + `demo_mode=false`. |
| `157d351` | Rewrote factuality: weighted/capped heuristic (attribution 0.35, absolute 0.20, specific 0.15, date 0.10, hedging ×0.4); whole-word evidence overlap; abbreviation-aware sentence splitting (`Dr.` no longer splits); `depth` now gates sample count. |
| `0ea2cab` | Router rewritten around impact class. `SHALLOW` now reachable for `customer_support + generate_text/draft_email`. Irreversible actions always route DEEP. |
| `d4455cc` | `asyncio.wait_for` per detector using the policy's `latency_budget_ms`; timeouts reported as `"<name> (timeout)"`. Session eviction via `last_seen`; `risk_history` bounded; `trajectory_window_turns` actually used (was hardcoded 3/6). |
| `3628c99` | SHA-256 hash chain (`prev_hash`, `row_hash`) + `verify_chain()` + `GET /api/audit/verify`. Metrics corrected: FP = alert a reviewer overturned, FN = allowed response a reviewer rejected, alert-to-incident = confirmed / reviewed alerts. |
| `23cca80` | Corrected text is re-run through **the same detectors and the same policy**; the correction is accepted only if the second pass genuinely lands on a softer decision. A corrector that lies about success is rejected and the original decision stands. |
| `d7ed606` | All remaining dead config wired: CORS origins, per-depth latency fallbacks, privacy/bias flag thresholds, `max_session_exposure` default, `host`/`port` via a `__main__` block. **0 of 21 dead.** |
| `0ce9a91` | 32 regression tests (see §7). |
| `5ea203a` | `datetime.utcnow()` → tz-aware `_utcnow()`; `@app.on_event` → `lifespan`. Zero warnings. |
| `47cb65c` | `demo/run_demo.py` still imported the removed multiplier — a break introduced by `2ba9a3e`. |

### Files added

```
src/correction/redact.py      span masking
src/detectors/judge.py        judge-model client
tests/test_regressions.py     23 unit-level regressions
tests/test_pipeline_regressions.py   9 end-to-end regressions
pytest.ini                    asyncio_mode = auto
```

---

## 6. Measured outcome

```
                  BEFORE    AFTER
precision          0.43      0.94
recall             0.56      0.94
FPR                0.65      0.05     (SOC target cited in the report: <0.10)
tests              33        65
dead config       14/21      0/21
states reachable   4 of 5    5 of 5
```

Per-detector after: factuality 1.00/1.00/0.00 · privacy 0.86/0.86/0.14 · bias 1.00/1.00/0.00.

**⚠️ Honesty caveat that must survive into the doc:** those 38 cases are the same ones used to *find*
the bugs, so 0.94 is partly fit to them. Treat as direction, not a validated benchmark. A judge
improvising fresh cases will get worse numbers. The single remaining privacy FN is
`"Customer name is Robert Chen, DOB 1978"` — unstructured PII, which genuinely needs Presidio/NER.

**Regression tests are real, not tautologies.** The pipeline suite was run against
`backup-before-rewrite` in a throwaway worktree: **8 of 9 failed on old code, all 9 pass now.** The
9th (`test_irreversible_action_is_not_released_on_a_redact`) passed before too — that hole was
*latent*, only opening after a threshold edit.

**Demo now demonstrates the report's centrepiece:** identical underlying answer →
`ALLOW` → `ESCALATE` → `BLOCK` as use case and action change. `ESCALATE` fired **zero** times across
26 pre-fix evaluations.

---

## 7. Reproducing

The venv used was session-scratchpad and is gone. Recreate:

```bash
cd "/home/akronim26/Desktop/Skills/Blockchain/Projects/aether"
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m pytest tests/ -q          # 65 passed
SENTINEL_AUDIT_DB_PATH=/tmp/demo.db .venv/bin/python demo/run_demo.py
```

Prove a regression test still bites:

```bash
git worktree add --detach /tmp/old backup-before-rewrite
cp tests/test_pipeline_regressions.py pytest.ini /tmp/old/tests/ 2>/dev/null
cp pytest.ini /tmp/old/
cp src/correction/redact.py /tmp/old/src/correction/
cp src/detectors/judge.py  /tmp/old/src/detectors/
cd /tmp/old && SENTINEL_AUDIT_DB_PATH=/tmp/old.db pytest tests/test_pipeline_regressions.py -q
# expect: 8 failed, 1 passed
git worktree remove --force /tmp/old
```

Enable the real judge model (turns on SelfCheckGPT sampling):

```bash
export SENTINEL_DEMO_MODE=false
export SENTINEL_LLM_API_KEY=sk-...
export SENTINEL_JUDGE_MODEL=gpt-4o-mini   # never the generating model
```

---

## 8. Design decisions a future session should NOT undo

1. **No risk multiplier.** Thresholds are indexed by impact class. Reintroducing a scalar multiplier
   re-breaks the [0,1] scale and re-opens P1. It also breaks the doc's own §5.4.3 stance ("why are
   you allowed to add risk scores together?") applied to §5.4.2.
2. **The policy engine scores every detection result**, not just `flagged` ones. This is what keeps
   interpretation (Risk Fabric) separate from decision (Policy Engine).
3. **The offline factuality heuristic is capped at 0.55** (`_HEURISTIC_CEILING`), deliberately below
   every block threshold. It can WARN but never BLOCK. Raising the cap without a judge model would
   be claiming precision the branch does not have.
4. **Corrections must be re-verified.** A corrector's self-declared `succeeded` is not trusted.
5. **`unacceptable` tier is unused on purpose** — it means *prohibited* under the AI Act. There is a
   regression test pinning this.
6. **Hash chain, not "immutable".** SQLite cannot prevent UPDATE/DELETE. The claim is tamper-*evidence*.
   Don't restore the `"""Insert immutable row."""` docstring.
7. Private/loopback IP ranges are deliberately **not** PII — that was the main privacy FP source.

---

## 9. Not done — ranked

1. **Update the doc.** §8.1 disclosures, §7.2 real numbers (C1), plus D1–D4 / F1–F5 above.
   Nothing in the `.docx` has been touched.
2. **Claim provenance / taint tracking** (E1) — highest-value remaining feature; makes §9's demo
   claim mechanically true and is a stronger originality claim than Action Impact.
3. **Microsoft Presidio** for unstructured PII — the brief explicitly names "dedicated PII/entity
   detection"; it's a `pip install` and the detector interface already supports the swap.
4. **Jurisdiction/geography policy axis** (C2) — ~15 lines, answers a named brief bullet.
5. **Shadow mode** as a policy field (`"mode": "shadow"` → compute, log, always ALLOW) — ~5 lines,
   turns §6.4's rollout narrative into a demonstrated capability.
6. **Policy Studio** — live threshold sliders re-running the same input. Dashboard already exists
   (`dashboard/app.js`).
7. `src/input_guardrail/guardrail.py` is still a **separate endpoint** (`POST /api/evaluate/input`),
   not inline in `/api/evaluate`. An integrator calling only the documented main endpoint gets zero
   input-side protection. §5.1's diagram shows it inline.
8. CoVe and BiasFilter correction are still `asyncio.sleep` stubs — they now *pass through the
   re-verification gate*, so they can't lie, but they don't actually correct anything.
9. `git push --force-with-lease` when the user decides to publish.
