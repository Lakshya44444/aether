import time
from typing import Any, Dict, List
from src.models.schemas import DetectionResult, RiskCategory
from src.detectors.base import BaseDetector
from src.config import config

class CostDetector(BaseDetector):
    """Cost estimation and retry tracking detector."""
    
    def __init__(self) -> None:
        self.session_costs: Dict[str, float] = {}
        self.session_inputs: Dict[str, List[str]] = {}
        self.pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "gpt-4o": {"prompt": 0.005, "completion": 0.015},
            "default": {"prompt": 0.001, "completion": 0.002}
        }
        
    @property
    def category(self) -> str:
        return RiskCategory.COST
        
    def _estimate_tokens(self, text: str) -> int:
        return int(len(text.split()) * 1.3)
        
    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        
        session_id = kwargs.get("session_id", "default_session")
        model_name = kwargs.get("model_name", config.llm_model)
        
        # Retry detection
        retry_count = 0
        if session_id not in self.session_inputs:
            self.session_inputs[session_id] = []
        
        if input_text in self.session_inputs[session_id]:
            retry_count = self.session_inputs[session_id].count(input_text)
        self.session_inputs[session_id].append(input_text)
        
        # Token estimation
        prompt_tokens = self._estimate_tokens(input_text)
        completion_tokens = self._estimate_tokens(output_text)
        
        model_pricing = self.pricing.get(model_name, self.pricing["default"])
        estimated_cost_usd = (prompt_tokens * model_pricing["prompt"] / 1000) + \
                             (completion_tokens * model_pricing["completion"] / 1000)
                             
        if session_id not in self.session_costs:
            self.session_costs[session_id] = 0.0
        self.session_costs[session_id] += estimated_cost_usd
        
        session_cost_usd = self.session_costs[session_id]
        
        score = 0.0
        if session_cost_usd >= config.cost_block_usd:
            score = 1.0
        elif session_cost_usd >= config.cost_warn_usd:
            score = 0.5
            
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
