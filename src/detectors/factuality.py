import time
import re
from typing import Any
from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector

class FactualityDetector(BaseDetector):
    """Two-branch factuality detector."""
    
    @property
    def category(self) -> str:
        return RiskCategory.FACTUALITY
        
    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        context_documents = kwargs.get("context_documents")
        
        claims = [c.strip() for c in re.split(r'(?<=[.!?])\s+', output_text) if c.strip()]
        if not claims:
            claims = [output_text]
            
        flagged_spans = []
        
        if context_documents:
            branch = "evidence"
            supported = 0
            unsupported = 0
            
            context_text = " ".join(context_documents).lower()
            
            for claim in claims:
                claim_words = set(re.findall(r'\b\w+\b', claim.lower()))
                significant_words = {w for w in claim_words if len(w) > 4}
                
                if not significant_words:
                    supported += 1
                    continue
                    
                overlap = sum(1 for w in significant_words if w in context_text)
                if overlap / len(significant_words) >= 0.3:
                    supported += 1
                else:
                    unsupported += 1
                    start_idx = output_text.find(claim)
                    if start_idx != -1:
                        flagged_spans.append(FlaggedSpan(
                            start=start_idx,
                            end=start_idx + len(claim),
                            text=claim,
                            categories=[RiskCategory.FACTUALITY],
                            severity=0.8,
                            detail="Claim lacks sufficient support in context."
                        ))
                        
            total_claims = len(claims)
            score = unsupported / total_claims if total_claims > 0 else 0.0
            
            details = {
                "branch": branch,
                "total_claims": total_claims,
                "supported": supported,
                "unsupported": unsupported,
                "claims_analysis": [{"claim": c} for c in claims]
            }
        else:
            branch = "consistency"
            
            suspect_claims = 0
            
            for claim in claims:
                is_suspect = False
                
                # Check for absolute claims
                if re.search(r'\b(always|never|guaranteed|impossible|certainly)\b', claim, re.IGNORECASE):
                    is_suspect = True
                    
                # Check for specific numbers or percentages without clear context
                if re.search(r'\d+(\.\d+)?%|\$\d+|\b(million|billion)\b', claim, re.IGNORECASE):
                    is_suspect = True
                    
                # Check for specific dates
                if re.search(r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b', claim, re.IGNORECASE):
                    is_suspect = True

                if is_suspect:
                    suspect_claims += 1
                    start_idx = output_text.find(claim)
                    if start_idx != -1:
                        # Hedging language reduces severity
                        severity = 0.9
                        if re.search(r'\b(might|may|could|possibly|appears to|perhaps)\b', claim, re.IGNORECASE):
                            severity = 0.4
                            
                        flagged_spans.append(FlaggedSpan(
                            start=start_idx,
                            end=start_idx + len(claim),
                            text=claim,
                            categories=[RiskCategory.FACTUALITY],
                            severity=severity,
                            detail="Highly specific or absolute claim without context."
                        ))
                        
            total_claims = len(claims)
            score = min(1.0, (suspect_claims / total_claims) * 1.5) if total_claims > 0 else 0.0
            
            details = {
                "branch": branch,
                "total_claims": total_claims,
                "suspect_claims": suspect_claims,
                "claims_analysis": [{"claim": c} for c in claims]
            }
            
        latency_ms = (time.time() - start_time) * 1000
        
        return DetectionResult(
            category=RiskCategory.FACTUALITY,
            score=score,
            flagged=score > 0.6,
            flagged_spans=flagged_spans,
            details=details,
            branch_used=branch,
            latency_ms=latency_ms
        )
