from src.models.schemas import InputGuardrailRequest, InputGuardrailResponse, Decision
from src.detectors.privacy import PrivacyDetector
from src.correction.redact import apply_redaction


class InputGuardrail:
    """Input-side guardrail that screens prompts before they reach the model."""

    def __init__(self) -> None:
        self.privacy_detector = PrivacyDetector()

    async def screen(self, request: InputGuardrailRequest) -> InputGuardrailResponse:
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

        spans = detection_result.flagged_spans
        return InputGuardrailResponse(
            decision=Decision.REDACT,
            reason=f"Detected and redacted {len(spans)} PII instances.",
            sanitized_text=apply_redaction(request.input_text, spans),
            flagged_spans=spans
        )
