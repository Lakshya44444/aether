import json
import os
import time
from collections import Counter
from typing import Any, Dict

from src.models.schemas import DetectionResult, RiskCategory
from src.detectors.base import BaseDetector
from src.config import config


class CostDetector(BaseDetector):
    """Cost estimation and retry tracking detector."""

    def __init__(self) -> None:
        self.session_costs: Dict[str, float] = {}
        self.session_inputs: Dict[str, Counter] = {}
        # Bounds the per-session retry table. A session that asks more distinct
        # questions than this stops counting retries rather than growing without limit.
        self.max_tracked_prompts = config.max_tracked_prompts
        self.pricing = self._load_pricing()

    @staticmethod
    def _load_pricing() -> Dict[str, Dict[str, float]]:
        """Reads the per-1K-token price table.

        Vendor prices change and have nothing to do with this code, so they live in a
        JSON file rather than in a dict here. `default` is what an unknown model costs.
        """
        path = config.model_pricing_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "model_pricing.json"
        )
        with open(path, "r", encoding="utf-8") as f:
            table = json.load(f)
        return {k: v for k, v in table.items() if not k.startswith("_")}

    @property
    def category(self) -> str:
        return RiskCategory.COST

    def _estimate_tokens(self, text: str) -> int:
        return int(len(text.split()) * 1.3)

    def forget_session(self, session_id: str) -> None:
        """Drops all accounting for a session once it is no longer live."""
        self.session_costs.pop(session_id, None)
        self.session_inputs.pop(session_id, None)

    def scan(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()

        session_id = kwargs.get("session_id") or "default_session"
        model_name = kwargs.get("model_name") or config.llm_model

        # Counter lookup is O(1); the previous list scan was O(n) per call, which made
        # a long session quadratic in the number of turns.
        seen = self.session_inputs.setdefault(session_id, Counter())
        retry_count = seen[input_text]
        if retry_count or len(seen) < self.max_tracked_prompts:
            seen[input_text] += 1

        prompt_tokens = self._estimate_tokens(input_text)
        completion_tokens = self._estimate_tokens(output_text)

        model_pricing = self.pricing.get(model_name, self.pricing["default"])
        estimated_cost_usd = (prompt_tokens * model_pricing["prompt"] / 1000) + \
                             (completion_tokens * model_pricing["completion"] / 1000)

        session_cost_usd = self.session_costs.get(session_id, 0.0) + estimated_cost_usd
        self.session_costs[session_id] = session_cost_usd

        score = 0.0
        if session_cost_usd >= config.cost_block_usd:
            score = 1.0
        elif session_cost_usd >= config.cost_warn_usd:
            score = 0.5

        # Repeated identical prompts mean the caller is retrying a failing interaction,
        # which is a cost signal in its own right regardless of the running total.
        if retry_count >= config.cost_retry_escalate_count:
            score = max(score, 0.75)

        latency_ms = (time.time() - start_time) * 1000

        details = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "session_cost_usd": session_cost_usd,
            "retry_count": retry_count
        }

        return DetectionResult(
            category=RiskCategory.COST,
            score=score,
            flagged=score > 0,
            details=details,
            latency_ms=latency_ms
        )
