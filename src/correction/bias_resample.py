import asyncio
from typing import List
from src.models.schemas import CorrectionResult, FlaggedSpan


class BiasResampler:
    """BiasFilter approximation.

    Stub: substitutes a neutral placeholder rather than resampling and rescoring
    alternatives. Output still has to pass the caller's re-verification gate.
    """

    async def resample(self, output_text: str, flagged_spans: List[FlaggedSpan]) -> CorrectionResult:
        if not flagged_spans:
            return CorrectionResult(attempted=False, succeeded=False, original_text=output_text)

        corrected_text = output_text
        for span in flagged_spans:
            corrected_text = corrected_text.replace(span.text, "[Neutralized alternative]")

        await asyncio.sleep(0.05)

        return CorrectionResult(
            attempted=True,
            succeeded=True,
            original_text=output_text,
            corrected_text=corrected_text,
            method="bias_resample"
        )
