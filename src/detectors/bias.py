import time
import re
from typing import Any
from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector

class BiasDetector(BaseDetector):
    """Bias and toxicity detector."""
    
    @property
    def category(self) -> str:
        return RiskCategory.BIAS
        
    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        
        patterns = {
            "stereotyping": [
                r'\b(all|most|typical|every)\s+(men|women|black|white|asian|hispanic|gay|straight)s?\s+(are|is)\b',
                r'\b(typical)\s+(male|female)\b'
            ],
            "consequential": [
                r'\b(should be (rejected|denied|fired|terminated|excluded))\b',
                r'\b(is not qualified|is incompetent)\b'
            ],
            "gender_bias": [
                r'\b(man up|act like a man|women’s work)\b'
            ],
            "age_bias": [
                r'\b(too old|too young|past their prime|boomer|millennial)\b'
            ]
        }
        
        flagged_spans = []
        max_score = 0.0
        
        for bias_type, regex_list in patterns.items():
            for pattern in regex_list:
                for match in re.finditer(pattern, output_text, re.IGNORECASE):
                    if bias_type == "consequential":
                        severity = 0.8
                    elif bias_type == "stereotyping":
                        severity = 0.6
                    else:
                        severity = 0.5
                        
                    max_score = max(max_score, severity)
                    
                    flagged_spans.append(FlaggedSpan(
                        start=match.start(),
                        end=match.end(),
                        text=match.group(),
                        categories=[RiskCategory.BIAS],
                        severity=severity,
                        detail=f"Detected {bias_type} language pattern."
                    ))
                    
        latency_ms = (time.time() - start_time) * 1000
        
        return DetectionResult(
            category=RiskCategory.BIAS,
            score=max_score,
            flagged=max_score >= 0.5,
            flagged_spans=flagged_spans,
            details={"matches": len(flagged_spans)},
            latency_ms=latency_ms
        )
