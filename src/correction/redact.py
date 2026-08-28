from typing import List

from src.models.schemas import FlaggedSpan


def _label(span: FlaggedSpan) -> str:
    """Derives the mask label from a detector's span detail, e.g. 'EMAIL' -> '[EMAIL]'."""
    if "type" in span.detail:
        return f"[{span.detail.split()[-1]}]"
    return "[REDACTED]"


def apply_redaction(text: str, spans: List[FlaggedSpan]) -> str:
    """Masks flagged spans in place.

    Spans are applied right to left so that replacing one does not shift the offsets
    of the spans still to be applied. Overlapping spans are collapsed to the widest
    enclosing span, since two detectors flagging the same characters must not produce
    a mask nested inside another mask.
    """
    if not spans:
        return text

    ordered = sorted(spans, key=lambda s: (s.start, -s.end))
    merged: List[FlaggedSpan] = []
    for span in ordered:
        if merged and span.start < merged[-1].end:
            if span.end > merged[-1].end:
                merged[-1] = merged[-1].model_copy(update={"end": span.end})
            continue
        merged.append(span)

    for span in reversed(merged):
        text = text[: span.start] + _label(span) + text[span.end :]
    return text
