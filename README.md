# Aether

Runtime governance for AI systems that take actions.

Aether is a working proof-of-concept AI runtime control plane: it sits between a
model and whatever the model is about to do, scores the output, weighs it against
its action and the session context, and returns one of five decisions — `ALLOW`,
`WARN`, `REDACT`, `ESCALATE`, `BLOCK` — with a signed trace of how it got there.

This repo implements the core control-plane pattern and decision logic in a way that
is credible for a hackathon and a strong foundation for production work. It does not
claim to fully solve every advanced research problem in the broader governance
document: several design ideas are represented as realistic approximations or
explicit future work rather than as production-complete systems.

```bash
curl -s localhost:8000/api/evaluate -H 'content-type: application/json' -d '{
  "input_text":  "Send the refund confirmation.",
  "output_text": "Refund of $8,400 approved by the CFO. Contact john@acme.com.",
  "use_case":    "finance_agent",
  "action":      "execute_payment",
  "session_id":  "demo-1"
}'
```

```json
{
  "decision": "BLOCK",
  "reason": "Factuality score 0.25 at severe impact (warn 0.2, block 0.45) triggered WARN;
             Privacy score 0.40 at severe impact (warn 0.05, block 0.15) triggered BLOCK",
  "corrected_output": null
}
```

---

## The idea

Most guardrails score text. Text is not the risk.

The same sentence is a non-event in a support chat, a compliance question in an
internal copilot, and an incident when it accompanies a payment. What separates them
is not the words — it is **what the system is about to do with them, and whether that
can be undone**.

Aether scores `impact × reversibility` and uses it to select which thresholds apply.

| Action                             | Impact          | Reversibility  | Class      |
| ---------------------------------- | --------------- | -------------- | ---------- |
| `generate_text`, `draft_email`     | low             | high           | `routine`  |
| `send_email`, `update_crm`         | medium          | medium         | `elevated` |
| `delete_record`, `execute_payment` | high / critical | low / very low | `severe`   |

A privacy score of 0.40 is a warning on `generate_text` and a block on
`execute_payment`, from the same detector output and the same policy file. Thresholds
are indexed by class rather than scaled by a multiplier, so every number in a policy
stays on the detector's own `[0, 1]` scale and can be read without recomputing
anything.

---

## Pipeline

```mermaid
flowchart TB
    REQ["<b>POST /api/evaluate</b> — input · output · use case · action · session<br/>load policy · resolve risk tier · route verification depth"]

    D1["<b>Factuality</b><br/>evidence · judge · heuristic"]
    D2["<b>Privacy</b><br/>PII · financial · national id"]
    D3["<b>Bias</b><br/>explicit · coded proxy"]
    D4["<b>Cost</b><br/>session spend · retries"]
    D5["<b>Injection</b><br/>the prompt, not the reply"]

    POL["<b>Risk fabric</b> — turn risk · session exposure · trajectory<br/><b>Policy engine</b> — thresholds by category × impact class<br/>a detector that failed or timed out takes the policy fail_mode"]

    DEC["<b>ALLOW</b> · <b>WARN</b> · <b>REDACT</b> · <b>ESCALATE</b> · <b>BLOCK</b>"]

    MASK["<b>Mask flagged spans</b><br/>at the offsets the detector reported"]
    CORR["<b>Correct, then re-verify</b> — same detectors, same policy<br/>kept only if it lands softer, and floored at WARN"]

    AUDIT[("<b>Hash-chained audit log</b> → response + decision trace")]

    REQ --> D1 & D2 & D3 & D4 & D5 --> POL --> DEC
    DEC -->|REDACT| MASK
    DEC -->|BLOCK · ESCALATE| CORR
    DEC -->|ALLOW · WARN| AUDIT
    MASK --> AUDIT
    CORR --> AUDIT
```

Three properties worth naming, because each one is a place this kind of system
usually cheats:

**A correction has to prove itself.** The correction layer rewrites flagged spans,
then the rewritten text goes back through the same detectors and the same policy. The
correction is kept only if that second pass genuinely lands on a softer decision, and
the decision is floored at `WARN` regardless — a caller must be able to see that the
text was changed.

**A failed detector is not a clean result.** A detector that raises or overruns its
budget produced no signal, so the policy's declared `fail_mode` decides. The failure
is recorded on the trace rather than being read as "nothing found".

**The audit log is tamper-evident, not immutable.** Rows are hash-chained and the head
is anchored, so mid-chain edits, tail truncation and forged review verdicts all break
verification. SQLite cannot prevent an `UPDATE`; the log does not claim it can.

---

## Scope and honesty

This project is intentionally scoped as a credible governance prototype rather than a
full production deployment of every advanced guardrail research technique.

The core concept is implemented and tested:

- runtime interception between model output and action execution
- use-case-aware policy decisions
- action impact × reversibility logic
- session exposure and trajectory tracking
- input-side screening and output-side mitigation
- decision trace and human-review workflow
- evaluation harnesses and metric reporting
- horizontal scaling behind Postgres and Redis, verified against real servers

The following are not claimed as fully solved production-grade components in this
repo, even though they are relevant to the broader architecture and serve as clear
future work:

- full fairness / bias modeling at research-grade rigor
- conformal prediction calibration with statistical guarantees
- full multi-sample factuality verification at production latency budgets
- advanced semantic caching, retrieval, and streaming verification
- enterprise-scale operations: retention, externally anchored chain heads, key
  management, and adversarial robustness

In other words, Aether demonstrates the runtime governance idea and the decision layer
that turns detection into action. The deeper research-grade capabilities are framed as
next-step extensions, not as fully shipped production claims.

---

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn src.main:app --reload
```

- Landing page — <http://localhost:8000>
- Operator console — <http://localhost:8000/console.html>
- OpenAPI — <http://localhost:8000/docs>

The gateway serves `frontend/out` when it exists and falls back to the static
`dashboard/`, so it always has a UI. Both are resolved against the repository root, so
it does not matter which directory you start uvicorn from.

Four connected scenarios, showing a session escalate across turns:

```bash
AETHER_AUDIT_DB_PATH=/tmp/demo.db .venv/bin/python demo/run_demo.py
```

### Frontend

```bash
cd frontend
npm install
npm run build      # emits frontend/out, served by the gateway
npm run dev        # or :3000 with hot reload against the API on :8000
```

---

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/evaluate` | POST | Full pipeline. Returns decision, reason, trace, corrected output |
| `/api/evaluate/input` | POST | Input guardrail alone — redacts PII, refuses injection |
| `/api/traces` | GET | Recent decision traces |
| `/api/traces/{id}` | GET | One trace |
| `/api/review` | POST | Human verdict on an escalated case |
| `/api/audit/verify` | GET | Recompute the hash chain, report the first bad row |
| `/api/stats` | GET | Decision counts, latency, alert-to-incident rate, reviewed trace ids |
| `/api/sessions/{id}` | GET | Exposure, trajectory, last decision |
| `/api/health` | GET | Liveness and readiness; checks both backends. Unauthenticated |
| `/api/metrics` | GET | Prometheus text format. Unauthenticated |
| `/api/policies/reload` | POST | Re-read the policy directory without a restart |

`/api/evaluate` screens the prompt as well as the completion. Input-side findings are
tagged, because their offsets index the prompt and must not reach the output
redaction path.

---

## Policies

One JSON file per use case in `src/policy_engine/policies/`, holding thresholds per
category per impact class, the actions that always meet a human, a session exposure
ceiling, a latency budget, and a fail mode.

| Use case           | Tier    | Fail mode   | PII    | Budget  |
| ------------------ | ------- | ----------- | ------ | ------- |
| `customer_support` | limited | fail open   | redact | 300 ms  |
| `internal_copilot` | high    | fail closed | redact | 500 ms  |
| `finance_agent`    | high    | fail closed | block  | 1000 ms |

`customer_support` fails open because a support reply blocked by a crashed detector is
a worse outcome than one that slipped through. `finance_agent` fails closed for the
opposite reason. That choice is the policy's to make, not the code's.

---

## Testing

Three layers, run separately because they answer different questions and fail for
different reasons.

```bash
.venv/bin/python -m pytest -q          # 82 tests; 9 skip without Postgres and Redis
.venv/bin/python -m evals.run          # detector and pipeline accuracy, with gates
.venv/bin/python scripts/verify.py     # adversarial: does the system do what the docs claim
```

**pytest** — regression tests that pin behaviour which was previously wrong, plus
contract tests that go through the real app. Nothing here mocks the component under
test.

**evals** — 104 labelled detector cases, 66 unseen-phrasing cases, and 28 end-to-end
pipeline cases. Every case
carries a split: `dev` is the only data tuning may look at, `test` is held out and is
what `evals/gates.json` is checked against. A test enforces that no case appears in
both. Pipeline cases carry a safety floor as well as an expected decision — landing
softer than the floor is the failure that matters; landing stricter is not.

**verify.py** — probes the claims the README and the design make, against the running
system, on a third set of cases used for nothing else. Exits non-zero while any claim
is unmet, so it works as a CI gate.

### Measured

Regenerate with `python scripts/export_metrics.py`, which writes both this block and
the numbers the landing page reads. Typed by hand before, and already drifting.

<!-- measured:start -->
| Detector | n | Precision | Recall | FPR | Recall on unseen phrasing |
|---|---|---|---|---|---|
| bias | 14 | 1.00 | 1.00 | 0.00 | 0.60 (n=20) |
| factuality/evidence | 6 | 1.00 | 0.67 | 0.00 | 0.60 (n=10) |
| factuality/heuristic | 4 | 1.00 | 0.50 | 0.00 | 0.60 (n=10) |
| injection | 9 | 1.00 | 1.00 | 0.00 | 0.88 (n=12) |
| privacy | 18 | 1.00 | 1.00 | 0.00 | 0.87 (n=24) |

End to end: 13 cases, exact decision match 1.00, 0 below the safety floor, p50 4 ms, p95 8 ms against budgets of 300–1000 ms.
<!-- measured:end -->

Two bars, measuring two different things. The held-out `test` split of
`detectors.jsonl` is genuinely held out — a test enforces that no case appears in both
splits — but it was authored alongside the patterns, so it measures whether they still
work, not whether they generalise. `unseen.jsonl` is written to deliberately different
phrasings (UK phone shapes, paraphrased injections, age bias containing no age word).

**The gap between the two columns is the honest number**, and closing it was a
rewrite, not more patterns. The detectors used to match fixed phrases, which is why
recall fell from 1.00 to 0.30 on bias and 0.25 on injection: a literal pattern catches
the wording it was written for and nothing else. They now match a **verb slot near an
object slot** — a cancel verb near an instruction noun, a group term near a
generalising frame — with the two halves found separately and only required to sit
close together. Paraphrase usually swaps the words in one slot and leaves the
structure, so the structure is what gets matched.

| | before | after |
|---|---|---|
| injection | 0.25 | **0.88** |
| privacy | 0.80 | **0.87** |
| bias | 0.30 | **0.60** |
| factuality | 0.60 | 0.60 |

All four at precision 1.00 and FPR 0.00 except factuality, and the held-out split did
not move except upward (privacy 0.91 → 1.00). Requiring both slots is also what keeps
the false alarms out: *ignore* is only an override next to an instruction noun, which
is why "ignore the typo in my last message" and "act as my travel agent" still score
zero.

**What did not move is the honest part.** Factuality is unchanged at 0.60, because its
gap is a modelling limit rather than a phrasing one — bag-of-words overlap cannot tell
an added attribution from a paraphrase, and an entailment judge is the upgrade. Bias
sits lowest of the four for the same kind of reason: what remains is carried entirely
in implication (*"put her on reception instead, it plays better with clients"*), and
there is no verb and no object to match on.

**And `unseen.jsonl` is no longer untouched.** It was, for exactly one measurement
round. Two false alarms it surfaced — "what are the rules for expensing travel", an
ordinary policy question, and "forget the earlier estimate", ordinary English — were
then fixed against it. That is tuning, and it means the numbers above are a regression
bound rather than a generalisation estimate. The next generalisation number worth
defending has to come from a corpus this project did not write.

Neither set is large enough to carry a confidence interval worth quoting: at n=9 a
single miss moves recall by 0.11. Treat all of this as a smoke test that fails loudly
on a regression, not as a measurement of production accuracy. Getting to a number
worth defending means a few hundred cases per detector drawn from a corpus this
project did not write — Presidio's PII suite, a public jailbreak collection, FEVER for
factuality — with the detectors frozen before the set is opened.

Gates sit just under these numbers so they fail on a regression rather than on noise,
including the unseen-phrasing floors. What is still missed is recorded in
`evals/gates.json` — each remaining gap named, with why a pattern cannot reach it.

---

## Configuration

Every field in `src/config.py` is settable through a `AETHER_`-prefixed environment
variable.

```bash
AETHER_PORT=8000
AETHER_AUDIT_DB_PATH=/var/lib/aether/audit.db
AETHER_MAX_TEXT_CHARS=100000        # request cap; detection is linear in length
AETHER_POLICIES_DIR=src/policy_engine/policies

AETHER_API_KEYS=                    # comma-separated; empty means unauthenticated
AETHER_CORS_ORIGINS=http://localhost:3000,http://localhost:8000
AETHER_RATE_LIMIT_PER_MINUTE=120    # 0 disables
AETHER_RATE_LIMIT_BURST=30          # tokens in hand; a burst under this is never limited
AETHER_AUDIT_DSN=                   # postgres; empty means the SQLite file above
AETHER_REDIS_URL=                   # empty means process-local session state
AETHER_LOG_JSON=true                # false for human-readable local logs
```

Offline, factuality falls back to a surface heuristic capped at 0.55 — it can warn,
never block on its own. To enable SelfCheckGPT-style consistency sampling:

```bash
AETHER_DEMO_MODE=false
AETHER_LLM_API_KEY=sk-...
AETHER_JUDGE_MODEL=gpt-4o-mini      # never the model that produced the output
```

---

## What this is not

A prototype that is honest about its edges, rather than a product.

- **Detectors are heuristics.** Slot and overlap based. They are measured, the
  numbers are above, and the numbers are not a model evaluator's.
- **Offline factuality cannot detect a plausible falsehood.** Without a judge model
  the heuristic branch reads surface shape only. It is capped below every block
  threshold precisely so it cannot act on a guess.
- **Injection detection is still pattern matching**, just structural rather than
  literal — a cancel verb near an instruction noun rather than a fixed phrase. Capped
  at 0.70 so it escalates rather than refuses. That covers paraphrase within a family
  (unseen recall 0.88); it does not cover an attack that states no cancel verb at all,
  and the unseen set has one it misses.
- **Tamper evidence has a ceiling.** The chain head lives in the file it protects.
  Anchoring it externally is the upgrade.
- **Storage defaults to a local SQLite file** with process-local session state, which
  is single-worker by construction. Postgres and Redis backends exist and are tested
  against real servers; the default is the appliance because it needs nothing else
  running. See *Deploying this* below.
- **Retention, chain anchoring and key management are not solved.** The audit head
  lives in the database it protects, nothing ages rows out, and keys are an
  environment variable.

---

## Deploying this

Two shapes. The difference between them is three environment variables, because those
are exactly the three things that make the process stateless.

### The appliance — one process, one volume

```bash
docker compose up --build          # needs AETHER_API_KEYS set
```

Correct for an internal gateway or a sidecar, and **the only shape where the session
controls work without extra infrastructure**. `session_tracker`, the cost detector's
accounting and the audit chain's lock all live inside one process, so a second worker
would fork the chain and split every conversation in half — both silently. The gateway
logs `single_worker_only` at startup whenever it is running this way.

### The scaled shape — Postgres, Redis, any number of workers

```bash
docker compose --profile scaled up --build
```

```bash
AETHER_AUDIT_DSN=postgresql://user:pass@host/aether   # chain append takes a row lock
AETHER_REDIS_URL=redis://host:6379/0                  # one view of every session
uvicorn src.main:app --workers 4
```

Postgres takes `SELECT ... FOR UPDATE` on the head row inside the append transaction,
which is a lock every worker respects; Redis holds session exposure and cost
accounting behind a TTL. Verified rather than asserted: 200 concurrent evaluations
across four worker processes leave `/api/audit/verify` reporting `intact: true` over
200 rows, and a five-turn session with no sticky routing accumulates exposure to 0.90
and escalates. `tests/test_backends.py` pins both against real servers.

### The API is a trust boundary

`/api/traces` returns the decision record for every session the gateway has seen. Set
a key, or it is an exfiltration endpoint:

```bash
AETHER_API_KEYS=$(openssl rand -hex 32)     # comma-separated; sent as X-API-Key
AETHER_CORS_ORIGINS=https://console.example.com
AETHER_RATE_LIMIT_PER_MINUTE=120            # per key, or per client IP without one
```

Multiple keys are accepted so a rotation can overlap: add the new one, move callers,
drop the old one. With `AETHER_API_KEYS` empty the `/api` routes are open and the
gateway logs `unauthenticated` at startup. `/api/health` and `/api/metrics` stay open
— a probe and a scraper are infrastructure, and neither describes traffic content.
The static UI is never gated.

### The audit log holds what your traffic held

Detected spans are masked before a row is written, so the log keeps offsets,
categories and severities but not the characters — including the `text` each span
quotes, which is the one substring guaranteed to be PII and the last copy that used to
be left in the clear. What the detectors miss is stored verbatim, so the
unseen-phrasing recall above is also the ceiling on how much of your PII this actually
masks. Treat the database as in-scope for whatever regime you are
under: encrypt the volume, set a retention window, and restrict direct access as well
as API access.

```bash
AETHER_AUDIT_REDACT_STORED_TEXT=true        # default; false only inside the boundary
```

### Operating it

| | |
|---|---|
| `GET /api/health` | Liveness and readiness. Touches the audit store, the state store and the policy set; 503 if any is unreachable. The container healthcheck uses it. |
| `GET /api/metrics` | Prometheus text format. Watch `aether_detector_failures_total` — a detector timing out silently changes every decision through `fail_mode`. |
| `GET /api/audit/verify` | Recomputes the chain and names the first bad row. The only thing that will tell you the log has been tampered with. Run it on a schedule. |
| `POST /api/policies/reload` | Re-reads the policy directory. A compliance change should not need a deployment. |

Logs are JSON lines on stdout (`AETHER_LOG_JSON=false` for human-readable). The
`decision` event carries the trace id, decision, policy, latency and scores — never
the prompt or the completion, so a log aggregator does not become the PII store the
audit log goes to the trouble of not being.

Two caveats worth knowing before you rely on a number: metrics counters are
per-process, so a scrape of a multi-worker deployment is one worker's view; and the
rate limiter is per-process too, so N workers permit N times the configured rate. Both
have their upgrade paths noted in the code. Put a global limit at the proxy.

### Still missing for a regulated deployment

Retention and deletion of audit rows, an externally anchored chain head (it currently
lives in the database it protects), and key management beyond a comma-separated
environment variable.

---

## Layout

```
src/
  main.py               FastAPI gateway; the pipeline lives here
  config.py             every knob, AETHER_-prefixed
  models/schemas.py     the shared contract
  detectors/            factuality, privacy, bias, cost, injection, judge client
  risk_fabric/          session exposure, trajectory, action profiles
  policy_engine/        threshold resolution + policies/*.json
  verification_router/  how much verification this request is worth
  correction/           CoVe revise, bias resample, span redaction
  audit/                hash-chained log — trace.py owns the chain rule,
                        backends.py puts the rows in SQLite or Postgres
  state/                per-session state: a dict, or Redis
  observability.py      JSON logs and Prometheus metrics
  ratelimit.py          per-caller token bucket
evals/                  labelled datasets, runner, gates
scripts/verify.py       adversarial claim checks
dashboard/              the operator console — plain HTML/CSS/JS, no build step
frontend/               Next.js landing page, exported static and served by the
                        gateway; its build copies dashboard/ into public/
Dockerfile              two stages: build the UI, then the gateway
docker-compose.yml      the appliance, and a `scaled` profile with Postgres + Redis
```

## Licence

MIT.
