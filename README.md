# SENTINEL

AI Runtime Control Plane for risk-aware governance of enterprise AI actions.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100.0+-green.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview
Sentinel is a policy-driven runtime layer for AI systems. It evaluates an interaction as a combination of:

- the model output,
- the use case context,
- the action being requested,
- session risk history,
- and the enterprise policy assigned to that action.

The system produces a governance decision among ALLOW, WARN, REDACT, ESCALATE, and BLOCK, with audit logging and correction hooks built in.

## Core Thesis
The key design principle is:

Action Impact × Reversibility

Sentinel does not treat the output text in isolation. It reasons over the action that the AI is about to take, the consequence of that action, how easily it can be reversed, and the policy context for that use case.

## System Architecture

```text
[User / App]
      |
      v
[Input Guardrail]
      |
      v
[LLM / Agent]
      |
      v
[Detectors]
  - Factuality
  - Privacy
  - Bias
  - Cost
      |
      v
[Risk Fabric]
  - current turn risk
  - session exposure
  - action impact
  - action reversibility
      |
      v
[Policy Engine]
      |
      +--> ALLOW / WARN / REDACT / ESCALATE / BLOCK
      |
      v
[Correction Layer]
      |
      v
[Audit Log + Dashboard]
```

## What is implemented in this repo
This repository contains a working prototype that includes:

- FastAPI endpoints for evaluation and review
- policy files per use case
- detector implementations for factuality, privacy, bias, and cost
- session tracking and risk trajectory logic
- verification depth routing
- audit logging and dashboard support
- a connected demo narrative showing the governance progression across multiple scenarios

## Research Foundation
This project is grounded in the references collected in [RESEARCH_REFERENCES.md](RESEARCH_REFERENCES.md), including work around:

1. SelfCheckGPT-style consistency checks
2. Chain-of-Verification for factuality review
3. Ragas-inspired claim decomposition and evidence assessment
4. verification routing and adaptive evaluation depth
5. guardrail and policy-based action governance

## Quick Start

### 1) Install dependencies

```bash
cd sentinel
pip install -r requirements.txt
```

### 2) Run the demo narrative

```bash
python -m demo.run_demo
```

### 3) Start the API server

```bash
python -m uvicorn src.main:app --reload
```

Then open:

- http://localhost:8000
- or the API endpoints under /api

## API Reference

The project currently exposes these runtime endpoints in [src/main.py](src/main.py):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/evaluate` | POST | Full Sentinel evaluation pipeline |
| `/api/evaluate/input` | POST | Input-side guardrail screening |
| `/api/traces` | GET | Recent trace history |
| `/api/traces/{trace_id}` | GET | Fetch a specific trace |
| `/api/review` | POST | Human review submission |
| `/api/stats` | GET | Dashboard summary statistics |
| `/api/sessions/{session_id}` | GET | Session state and exposure summary |

## Use Cases
The repo includes three main policy-driven enterprise scenarios:

- `customer_support`: public-facing support chatbot
- `internal_copilot`: employee-facing internal workflow
- `finance_agent`: high-impact payment and transaction execution

Each use case has its own JSON policy under [src/policy_engine/policies](src/policy_engine/policies).

## Configuration
Runtime settings are centralized in [src/config.py](src/config.py). Configuration can be overridden using environment variables with the `SENTINEL_` prefix, such as:

- `SENTINEL_DEMO_MODE`
- `SENTINEL_LLM_API_KEY`
- `SENTINEL_PORT`
- `SENTINEL_POLICIES_DIR`

## Validation
The project was checked using the actual runtime and test suite:

```bash
python -m pytest -q
python -m demo.run_demo
```

Current verification status:

- pytest: 33 passed
- demo run: successful, with the expected governance sequence: WARN → ESCALATE → BLOCK

## Scope and Current Limitations
This is a working prototype, not a full production deployment. The current repo includes:

- real policy logic and governance flow,
- realistic heuristic detectors,
- session-aware risk scoring,
- and a functional demo/dashboard surface.

Current limitations:

1. Detectors are prototype-grade heuristics rather than production-grade model evaluators.
2. Audit logging uses the local SQLite-backed logger in the codebase.
3. The governance system is intentionally policy-aware, but not yet connected to a production LLM evaluation platform.
4. The app is designed for enterprise governance demonstration and simulation, not full autonomous agent execution in production.

## Roadmap
- Expand detector fidelity and evidence-based factuality checks
- Add richer policy authoring and approval workflows
- Connect with production LLM and model observability stacks
- Improve dashboard analytics and incident review UX

## License
MIT
