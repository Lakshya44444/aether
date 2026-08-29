"""
Sentinel — AI Runtime Control Plane
Application configuration.

All settings can be overridden via environment variables prefixed with SENTINEL_.
For example: SENTINEL_DEMO_MODE=false, SENTINEL_LLM_API_KEY=sk-...
"""
from pydantic_settings import BaseSettings
from typing import Optional


class SentinelConfig(BaseSettings):
    """Central configuration for the Sentinel runtime."""

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

    # ── Session Tracking ─────────────────────────────────────────
    max_session_exposure: float = 1.0
    session_timeout_minutes: int = 30
    trajectory_window_turns: int = 3

    # ── Audit / Storage ──────────────────────────────────────────
    audit_db_path: str = "sentinel_audit.db"

    # ── Request limits (trust boundary) ──────────────────────────
    # Detection is regex-bound and linear in input length, so an uncapped body is a
    # denial-of-service vector: one 4 MB request occupied the gateway for seconds.
    max_text_chars: int = 100_000
    max_context_documents: int = 50

    # ── Server ───────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"

    # ── Policy directory ─────────────────────────────────────────
    policies_dir: str = "src/policy_engine/policies"

    model_config = {"env_prefix": "SENTINEL_"}


# Singleton — importable from anywhere
config = SentinelConfig()
