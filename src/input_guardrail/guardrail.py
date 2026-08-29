from src.models.schemas import InputGuardrailRequest, InputGuardrailResponse, Decision
from src.detectors.privacy import PrivacyDetector
from src.detectors.injection import InjectionDetector
from src.correction.redact import apply_redaction


class InputGuardrail:
    """Input-side guardrail that screens prompts before they reach the model.

    Two separate questions: does the prompt leak personal data (redactable), and is it
    trying to take over the model (not redactable -- an injection with its trigger words
    masked is still an injection, so it is refused rather than sanitised).
    """

    def __init__(self) -> None:
        self.privacy_detector = PrivacyDetector()
        self.injection_detector = InjectionDetector()

    async def screen(self, request: InputGuardrailRequest) -> InputGuardrailResponse:
        privacy = await self.privacy_detector.detect(input_text="", output_text=request.input_text)
        injection = await self.injection_detector.detect(request.input_text, "")

        if injection.flagged:
            families = ", ".join(injection.details.get("families", [])) or "unknown"
            return InputGuardrailResponse(
                decision=Decision.BLOCK,
                reason=f"Prompt injection detected ({families}); prompt not forwarded.",
                sanitized_text=None,
                flagged_spans=injection.flagged_spans + privacy.flagged_spans,
            )

        if not privacy.flagged:
            return InputGuardrailResponse(
                decision=Decision.ALLOW,
                reason="No PII or injection detected in input.",
                sanitized_text=request.input_text,
                flagged_spans=[],
            )

        spans = privacy.flagged_spans
        return InputGuardrailResponse(
            decision=Decision.REDACT,
            reason=f"Detected and redacted {len(spans)} PII instances.",
            sanitized_text=apply_redaction(request.input_text, spans),
            flagged_spans=spans,
        )
