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

    # ── Session Tracking ─────────────────────────────────────────
    max_session_exposure: float = 1.0
    session_timeout_minutes: int = 30
    trajectory_window_turns: int = 3

    # ── Audit / Storage ──────────────────────────────────────────
    # Default to a writable temp directory so local runs do not fail on machines where
    # the repo root or current working directory is on a full or restricted volume.
    audit_db_path: str = os.path.join(tempfile.gettempdir(), "aether_audit.db")

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

    # ── Static UI ────────────────────────────────────────────────
    # Empty means: use the built frontend export if it exists, otherwise the plain
    # dashboard. Both are resolved against the repository root rather than the working
    # directory, so the gateway serves its UI whatever you start it from.
    frontend_dir: str = ""

    model_config = {"env_prefix": "AETHER_"}


# Singleton — importable from anywhere
config = AetherConfig()
