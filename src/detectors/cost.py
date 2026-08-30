import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

from src.models.schemas import DetectionResult, RiskCategory
from src.detectors.base import BaseDetector
from src.state import StateStore, MemoryStore
from src.config import config


class CostDetector(BaseDetector):
    """Cost estimation and retry tracking detector.

    Overrides `detect` rather than `scan` because its accounting lives in a
    `StateStore` and reading it is genuinely async. The computation itself is a
    handful of arithmetic -- no regex, nothing that holds the CPU -- so there is
    nothing to hand to a worker thread.

    Per-session state used to be two dicts on this object, which leaked (nothing ever
    evicted them, and each entry held a Counter of whole prompts) and made the gateway
    single-process. The store gives it a TTL and, behind Redis, one view shared by
    every worker.
    """

    def __init__(self, store: Optional[StateStore] = None) -> None:
        # Not `store or MemoryStore()`: MemoryStore defines __len__, so an empty
        # store is falsy and would be replaced by a private one. See SessionTracker.
        self.store = MemoryStore() if store is None else store
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

    @property
    def _ttl(self) -> int:
        return config.session_timeout_minutes * 60

    @staticmethod
    def _key(session_id: str) -> str:
        return f"cost:{session_id}"

    def _estimate_tokens(self, text: str) -> int:
        return int(len(text.split()) * 1.3)

    async def forget_session(self, session_id: str) -> None:
        """Drops all accounting for a session once it is no longer live."""
        await self.store.delete(self._key(session_id))

    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()

        session_id = kwargs.get("session_id") or "default_session"
        model_name = kwargs.get("model_name") or config.llm_model

        prompt_tokens = self._estimate_tokens(input_text)
        completion_tokens = self._estimate_tokens(output_text)

        model_pricing = self.pricing.get(model_name, self.pricing["default"])
        estimated_cost_usd = (prompt_tokens * model_pricing["prompt"] / 1000) + \
                             (completion_tokens * model_pricing["completion"] / 1000)

        # `prompts` is a dict rather than a Counter so it survives a JSON round trip.
        # The lookup is still O(1); the original list scan made a long session
        # quadratic in the number of turns.
        #
        # Keyed by digest, not by the prompt. Retry counting needs equality and nothing
        # else, so storing the text bought nothing and cost two things. It put raw
        # prompts in the state store in the clear -- the one place PII was still kept
        # verbatim, while the audit log goes to the trouble of masking it and the log
        # lines omit it entirely. And it made the value unbounded in practice: a request
        # may carry `max_text_chars` (100 KB by default) and `max_tracked_prompts` is
        # 512, so one session could hold ~51 MB that every subsequent turn re-parsed and
        # re-serialised. A digest is 64 characters whatever the prompt was.
        prompt_digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        seen_before = 0

        def mutate(raw: Optional[dict]) -> dict:
            nonlocal seen_before
            state = raw or {"cost_usd": 0.0, "prompts": {}}
            prompts = state["prompts"]
            seen_before = prompts.get(prompt_digest, 0)
            if seen_before or len(prompts) < self.max_tracked_prompts:
                prompts[prompt_digest] = seen_before + 1
            state["cost_usd"] = state["cost_usd"] + estimated_cost_usd
            return state

        state = await self.store.read_modify_write(self._key(session_id), self._ttl, mutate)
        session_cost_usd = state["cost_usd"]
        retry_count = seen_before

        score = 0.0
        if session_cost_usd >= config.cost_block_usd:
            score = 1.0
        elif session_cost_usd >= config.cost_warn_usd:
            score = 0.5

        # Repeated identical prompts mean the caller is retrying a failing interaction,
        # which is a cost signal in its own right regardless of the running total.
        if retry_count >= config.cost_retry_escalate_count:
            score = max(score, 0.75)

        return DetectionResult(
            category=RiskCategory.COST,
            score=score,
            flagged=score > 0,
            details={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": estimated_cost_usd,
                "session_cost_usd": session_cost_usd,
                "retry_count": retry_count,
            },
            latency_ms=(time.time() - start_time) * 1000,
        )
