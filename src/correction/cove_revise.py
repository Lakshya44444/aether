from typing import List, Optional
from src.models.schemas import CorrectionResult, FlaggedSpan


class CoVeReviser:
    """CoVe-style factuality correction.

    Stub: marks flagged claims as unverified rather than generating and answering
    verification questions. Output still has to pass the caller's re-verification
    gate, so it cannot smuggle through a correction it did not make.
    """

    async def revise(self, output_text: str, flagged_spans: List[FlaggedSpan], context_documents: Optional[List[str]] = None) -> CorrectionResult:
        if not flagged_spans:
            return CorrectionResult(attempted=False, succeeded=False, original_text=output_text)

        corrected_text = output_text
        for span in flagged_spans:
            corrected_text = corrected_text.replace(span.text, f"[{span.text} - could not be verified]")

        return CorrectionResult(
            attempted=True,
            succeeded=True,
            original_text=output_text,
            corrected_text=corrected_text,
            method="cove_revise"
        )
