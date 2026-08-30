"""
Aether — AI Runtime Control Plane
Application configuration.

All settings can be overridden via environment variables prefixed with AETHER_.
For example: AETHER_DEMO_MODE=false, AETHER_LLM_API_KEY=sk-...
"""
import os
import tempfile

from pydantic_settings import BaseSettings
from typing import Optional


class AetherConfig(BaseSettings):
    """Central configuration for the Aether runtime."""

    # ── Mode ──────────────────────────────────────────────────────
    demo_mode: bool = True  # True = heuristic/simulated detectors; False = real LLM calls

    # ── LLM API (for non-demo mode factuality/correction) ────────
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    judge_model: str = "gpt-4o-mini"  # Never the generating model
    judge_max_tokens: int = 256
    judge_samples: int = 3
    judge_timeout_s: float = 5.0

    # ── Latency Budgets (ms) ──────────────────────────────
    shallow_latency_budget_ms: int = 200   # Galileo Luna-2 class
    medium_latency_budget_ms: int = 700    # Multi-sample consistency
    deep_latency_budget_ms: int = 1000     # Patronus Glider class

    # ── Detection Thresholds (defaults, overridable per policy) ──
    factuality_threshold: float = 0.35
    evidence_overlap_threshold: float = 0.45
    privacy_threshold: float = 0.25
    bias_threshold: float = 0.4
    injection_threshold: float = 0.5
    cost_warn_usd: float = 0.50
    cost_block_usd: float = 2.00
    cost_retry_escalate_count: int = 3
    # Bounds the per-session retry table so a long session cannot grow without limit.
    max_tracked_prompts: int = 512
    # USD per 1K tokens. Empty means the table shipped beside the cost detector.
    model_pricing_path: str = ""

    # ── Risk fabric ──────────────────────────────────────────────
    # Session exposure is a damped governance heuristic, not a probability. These two
    # weights are what "damped" means: how much of this turn's risk is added, and how
    # much of the accumulated exposure carries into the next turn.
    exposure_turn_weight: float = 0.6
    exposure_decay: float = 0.55
    # How much the windowed average must move before a session is called rising or
    # falling rather than stable.
    trajectory_delta: float = 0.05

    # ── Policy fallbacks ─────────────────────────────────────────
    # Applied only when a policy file declares no thresholds for a category.
    default_warn_threshold: float = 0.5
    default_block_threshold: float = 0.8

    # ── Detector ceilings ────────────────────────────────────────
    # Caps on branches that infer from surface shape rather than evidence. Both sit
    # below the block thresholds in every shipped policy, so a pattern match or a
    # surface guess can raise a decision but never stop traffic on its own. Raising
    # either above a policy's block threshold changes that guarantee.
    factuality_heuristic_ceiling: float = 0.55
    injection_ceiling: float = 0.70

    # ── Factuality heuristic weights ─────────────────────────────
    # What each surface feature contributes to a claim's suspicion score when no judge
    # model is configured. These are hand-fitted tuning parameters, not structural
    # constants -- they are the first thing anyone retunes when the false-alarm rate
    # moves, which is why they live here rather than inside the detector.
    fact_weight_attribution: float = 0.35
    fact_weight_absolute: float = 0.20
    fact_weight_specific: float = 0.15
    fact_weight_date: float = 0.10
    # Hedged claims are asserted less strongly, so their weight is scaled down.
    fact_hedge_multiplier: float = 0.40
    # A claim scoring at or above this is worth reporting as a span.
    fact_span_threshold: float = 0.35

    # ── Shared state ─────────────────────────────────────────────
    # Redis URL for session exposure and cost accounting. Empty keeps both in a
    # process-local dict -- fine for one worker, silently wrong for more than one,
    # because turn 2 of a conversation can land on a process that never saw turn 1.
    redis_url: str = ""

    # ── Session Tracking ─────────────────────────────────────────
    max_session_exposure: float = 1.0
    session_timeout_minutes: int = 30
    trajectory_window_turns: int = 3

    # ── Audit / Storage ──────────────────────────────────────────
    # Default to a writable temp directory so local runs do not fail on machines where
    # the repo root or current working directory is on a full or restricted volume.
    audit_db_path: str = os.path.join(tempfile.gettempdir(), "aether_audit.db")
    # Postgres DSN. Empty means the local SQLite file above, which is correct for a
    # single-worker appliance and unsafe for anything else: SQLite appends are ordered
    # by a lock inside one process, so a second worker forks the chain. Postgres takes
    # a row lock every worker respects. See README, "Deploying this".
    audit_dsn: str = ""
    # The audit log is a governance record, not a copy of the traffic. Persisting raw
    # prompts and completions turned the log into the largest PII store in the system
    # -- and /api/traces served it. Detected spans are masked before a row is written;
    # the offsets, categories and severities stay, so a trace is still reviewable.
    # Set false only where the log itself is inside the compliance boundary.
    audit_redact_stored_text: bool = True

    # ── Request limits (trust boundary) ──────────────────────────
    # Detection is regex-bound and linear in input length, so an uncapped body is a
    # denial-of-service vector: one 4 MB request occupied the gateway for seconds.
    max_text_chars: int = 100_000
    max_context_documents: int = 50

    # ── Server ───────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    # Comma-separated. "*" is a development convenience: with no credentials in play it
    # still lets any page on the internet read /api/traces from a browser, so it is not
    # the default.
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    # ── Authentication (trust boundary) ──────────────────────────
    # Comma-separated keys accepted in the X-API-Key header on every /api route.
    # Empty disables authentication and logs a warning at startup: convenient for local
    # work, never correct for a deployment, because /api/traces returns decision
    # records for every session the gateway has seen.
    api_keys: str = ""

    # ── Rate limiting (trust boundary) ───────────────────────────
    # Per-key when a key is presented, per-client-IP otherwise. Detection is linear in
    # input length and a body may be 100 KB, so an unlimited caller is a CPU exhaustion
    # vector even with authentication in place. 0 disables it.
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 30

    # ── Observability ────────────────────────────────────────────
    log_level: str = "INFO"
    # JSON lines on stdout. False restores human-readable logs for local work.
    log_json: bool = True

    # ── Policy directory ─────────────────────────────────────────
    policies_dir: str = "src/policy_engine/policies"

    # ── Static UI ────────────────────────────────────────────────
    # Empty means: use the built frontend export if it exists, otherwise the plain
    # dashboard. Both are resolved against the repository root rather than the working
    # directory, so the gateway serves its UI whatever you start it from.
    frontend_dir: str = ""

    model_config = {"env_prefix": "AETHER_"}


# Singleton — importable from anywhere
config = AetherConfig()
