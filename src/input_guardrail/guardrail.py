from src.models.schemas import InputGuardrailRequest, InputGuardrailResponse, Decision
from src.detectors.privacy import PrivacyDetector

class InputGuardrail:
    """Input-side guardrail that screens prompts before they reach the model."""
    
    def __init__(self) -> None:
        self.privacy_detector = PrivacyDetector()
        
    async def screen(self, request: InputGuardrailRequest) -> InputGuardrailResponse:
        # Run privacy detector on the input text
        detection_result = await self.privacy_detector.detect(
            input_text="", 
            output_text=request.input_text
        )
        
        if not detection_result.flagged:
            return InputGuardrailResponse(
                decision=Decision.ALLOW,
                reason="No PII detected in input.",
                sanitized_text=request.input_text,
                flagged_spans=[]
            )
            
        sanitized_text = request.input_text
        # Sort spans in reverse to not mess up indices during replacement
        spans = sorted(detection_result.flagged_spans, key=lambda s: s.start, reverse=True)
        
        for span in spans:
            pii_type = span.detail.split()[-1] if "type" in span.detail else "REDACTED"
            replacement = f"[{pii_type}]"
            sanitized_text = sanitized_text[:span.start] + replacement + sanitized_text[span.end:]
            
        return InputGuardrailResponse(
            decision=Decision.REDACT,
            reason=f"Detected and redacted {len(spans)} PII instances.",
            sanitized_text=sanitized_text,
            flagged_spans=detection_result.flagged_spans
        )
