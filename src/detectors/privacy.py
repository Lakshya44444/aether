import time
import re
from typing import Any
from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector

class PrivacyDetector(BaseDetector):
    """PII Privacy detector."""
    
    @property
    def category(self) -> str:
        return RiskCategory.PRIVACY
        
    def _luhn_check(self, card_num: str) -> bool:
        digits = [int(c) for c in card_num if c.isdigit()]
        if len(digits) < 13 or len(digits) > 19:
            return False
        checksum = 0
        is_second = False
        for digit in reversed(digits):
            if is_second:
                digit *= 2
                if digit > 9:
                    digit -= 9
            checksum += digit
            is_second = not is_second
        return checksum % 10 == 0

    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        
        text_to_check = output_text
        
        patterns = {
            "EMAIL": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "PHONE": r'\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
            "SSN": r'\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b',
            "API_KEY": r'\b(sk-[a-zA-Z0-9]{20,}|key_[a-zA-Z0-9]{20,})\b',
            "IP_ADDRESS": r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b',
            "CREDIT_CARD": r'\b(?:\d{4}[-\s]?){3}\d{4}\b'
        }
        
        flagged_spans = []
        found_types = set()
        
        for pii_type, pattern in patterns.items():
            for match in re.finditer(pattern, text_to_check):
                text_match = match.group()
                
                # Validation for credit card
                if pii_type == "CREDIT_CARD" and not self._luhn_check(text_match):
                    continue
                    
                found_types.add(pii_type)
                flagged_spans.append(FlaggedSpan(
                    start=match.start(),
                    end=match.end(),
                    text=text_match,
                    categories=[RiskCategory.PRIVACY],
                    severity=1.0,
                    detail=f"Found PII of type {pii_type}"
                ))
                
        # Address matching (basic)
        address_pattern = r'\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln)\b'
        for match in re.finditer(address_pattern, text_to_check, re.IGNORECASE):
            found_types.add("ADDRESS")
            flagged_spans.append(FlaggedSpan(
                start=match.start(),
                end=match.end(),
                text=match.group(),
                categories=[RiskCategory.PRIVACY],
                severity=0.8,
                detail="Found PII of type ADDRESS"
            ))
            
        count_pii = len(flagged_spans)
        score = min(1.0, count_pii * 0.3)
        
        latency_ms = (time.time() - start_time) * 1000
        
        return DetectionResult(
            category=RiskCategory.PRIVACY,
            score=score,
            flagged=count_pii > 0,
            flagged_spans=flagged_spans,
            details={"found_types": list(found_types), "count": count_pii},
            latency_ms=latency_ms
        )
