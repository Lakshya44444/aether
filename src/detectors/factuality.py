import asyncio
import difflib
import re
import time
from typing import Any, List

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory, VerificationDepth
from src.detectors.base import BaseDetector
from src.detectors import judge
from src.config import config

# Abbreviated honorifics and company suffixes end in a period without ending a
# sentence; splitting on them detached "Dr." from the name it introduces.
_ABBREV = (r'(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bProf\.)'
           r'(?<!\bInc\.)(?<!\bLtd\.)(?<!\bSt\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)')
_SENTENCE_SPLIT = re.compile(_ABBREV + r'(?<=[.!?])\s+')
_WORD = re.compile(r'\b\w+\b')

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "your", "their", "have",
    "been", "will", "would", "could", "should", "about", "into", "than", "then",
    "they", "them", "there", "these", "those", "which", "while", "were", "was",
}

# Hedging marks a claim the model is not asserting outright.
_HEDGE = re.compile(
    r'\b(might|may|could|possibly|perhaps|appears to|seems to|likely|approximately|'
    r'roughly|around|about|i think|i believe|unverified|unconfirmed)\b',
    re.IGNORECASE,
)

# Surface features that correlate weakly with fabrication, used only as a fallback.
_ABSOLUTE = re.compile(r'\b(always|never|guaranteed|impossible|certainly|definitely)\b', re.IGNORECASE)
_SPECIFIC = re.compile(r'\d+(?:\.\d+)?%|\$\s?\d|\b(?:million|billion|trillion)\b', re.IGNORECASE)
_DATE = re.compile(
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
    r'Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b',
    re.IGNORECASE,
)
# Attribution to a named source is the strongest cheap signal of an unverifiable claim.
_ATTRIBUTION = re.compile(
    r'\b(?:according to|confirmed by|approved by|authorised by|authorized by|'
    r'signed off(?:\s+by)?|as stated by|as per)\b|'
    r'\b(?:CEO|CFO|CTO|COO|board|legal|management|underwriting|compliance|'
    r'the team)\s+(?:has\s+)?(?:confirmed|approved|stated|said|signed|agreed|cleared)\b|'
    r'\b(?:Dr|Mr|Ms|Mrs|Prof)\.?\s+[A-Z][a-z]+',
    re.IGNORECASE,
)

# The heuristic branch cannot see truth, only shape. It is capped below any policy's
# block threshold so a surface guess can raise a warning but never block on its own;
# blocking on factuality requires the judge branch. Tunable, because what counts as
# "below every block threshold" depends on the policies actually deployed.
_HEURISTIC_CEILING = config.factuality_heuristic_ceiling


def _split_claims(text: str) -> List[str]:
    claims = [c.strip() for c in _SENTENCE_SPLIT.split(text) if c.strip()]
    return claims or ([text] if text.strip() else [])


# Figures and names are the parts of a claim a paraphrase does not change. Bag-of-word
# overlap cannot see them: "the window is 30 days" and "the window is 400 days" share
# every word longer than three characters, so a contradicted number scored as supported.
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
_PROPER = re.compile(r'\b[A-Z][a-z]{2,}\b')


def _numbers(text: str) -> set:
    """Figures, normalised so 1,240 and $1,240.00 are the same figure."""
    found = set()
    for match in _NUMBER.finditer(text):
        value = match.group().replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        found.add(value or "0")
    return found


def _proper_nouns(text: str) -> set:
    """Capitalised words that are not just the first word of a sentence."""
    names = set()
    for sentence in _SENTENCE_SPLIT.split(text):
        tokens = _PROPER.findall(sentence)
        first = sentence.strip().split(" ")[0].strip(".,!?") if sentence.strip() else ""
        names |= {t for t in tokens if t != first}
    return names


def _content_words(text: str) -> set:
    return {
        w for w in (m.group().lower() for m in _WORD.finditer(text))
        if len(w) > 3 and w not in _STOPWORDS
    }


class FactualityDetector(BaseDetector):
    """Two-branch factuality detector.

    Branch A (evidence): claims are checked against supplied context documents.
    Branch B (consistency): the same question is answered several times by a judge
    model and the answers are compared; a claim the model actually knows is restated
    consistently, a confabulated one is not.

    When no judge is configured, Branch B degrades to a surface heuristic that is
    explicitly capped and labelled as such on the result, rather than pretending to
    the same authority.
    """

    @property
    def category(self) -> str:
        return RiskCategory.FACTUALITY

    def _span(self, output_text: str, claim: str, severity: float, detail: str):
        start = output_text.find(claim)
        if start == -1:
            return None
        return FlaggedSpan(
            start=start,
            end=start + len(claim),
            text=claim,
            categories=[RiskCategory.FACTUALITY],
            severity=severity,
            detail=detail,
        )

    def _evidence_branch(self, output_text: str, claims, context_documents):
        """Ragas-style coverage: fraction of claims the context does not support."""
        context_words = set()
        context_numbers = set()
        context_names = set()
        for doc in context_documents:
            context_words |= _content_words(doc)
            context_numbers |= _numbers(doc)
            context_names |= _proper_nouns(doc)

        supported = unsupported = 0
        spans = []
        for claim in claims:
            words = _content_words(claim)
            if not words:
                supported += 1
                continue

            # A figure or a name the context does not contain is a specific, checkable
            # contradiction, so it outranks the overlap score rather than averaging
            # into it.
            novel_numbers = _numbers(claim) - context_numbers
            novel_names = _proper_nouns(claim) - context_names
            # Whole-word overlap. Substring matching previously counted "account" as
            # supported by "accountant".
            overlap = len(words & context_words) / len(words)

            if novel_numbers or novel_names:
                detail = "Claim states {} the context does not contain: {}.".format(
                    "a figure" if novel_numbers else "a name",
                    ", ".join(sorted(novel_numbers or novel_names)),
                )
                severity = 0.8
            elif overlap < config.evidence_overlap_threshold:
                detail = f"Claim unsupported by context (overlap {overlap:.2f})."
                severity = min(1.0, 0.6 + (config.evidence_overlap_threshold - overlap))
            else:
                supported += 1
                continue

            unsupported += 1
            span = self._span(output_text, claim, severity=severity, detail=detail)
            if span:
                spans.append(span)

        total = len(claims)
        score = unsupported / total if total else 0.0
        return score, spans, {
            "branch": "evidence",
            "total_claims": total,
            "supported": supported,
            "unsupported": unsupported,
        }

    async def _consistency_branch(self, input_text: str, output_text: str, samples: int):
        """SelfCheckGPT-style agreement between independently sampled answers."""
        answers = await judge.sample_answers(input_text, samples, config.judge_timeout_s)
        similarities = [
            difflib.SequenceMatcher(None, output_text.lower(), a.lower()).ratio()
            for a in answers
        ]
        agreement = sum(similarities) / len(similarities)
        score = max(0.0, min(1.0, 1.0 - agreement))

        spans = []
        if score >= 0.5:
            span = self._span(
                output_text, output_text.strip(), severity=score,
                detail=f"Response disagrees with {len(answers)} independent samples "
                       f"(agreement {agreement:.2f}).",
            )
            if span:
                spans.append(span)

        return score, spans, {
            "branch": "consistency",
            "samples": len(answers),
            "agreement": round(agreement, 3),
        }

    def _heuristic_branch(self, output_text: str, claims):
        """Surface-shape fallback used when no judge model is available.

        Weighted and capped rather than binary: the previous version scored 1.00 for
        any sentence containing a number, a date or the word "always", which flagged
        ordinary support replies at maximum risk while missing fabrications that
        happened to contain neither.
        """
        suspect = 0.0
        spans = []
        for claim in claims:
            weight = 0.0
            if _ATTRIBUTION.search(claim):
                weight += config.fact_weight_attribution
            if _ABSOLUTE.search(claim):
                weight += config.fact_weight_absolute
            if _SPECIFIC.search(claim):
                weight += config.fact_weight_specific
            if _DATE.search(claim):
                weight += config.fact_weight_date
            if _HEDGE.search(claim):
                weight *= config.fact_hedge_multiplier
            weight = min(weight, 1.0)
            suspect += weight
            if weight >= config.fact_span_threshold:
                span = self._span(
                    output_text, claim, severity=min(weight, _HEURISTIC_CEILING),
                    detail="Unhedged attributed or absolute claim, unverified.",
                )
                if span:
                    spans.append(span)

        total = len(claims)
        raw = suspect / total if total else 0.0
        return min(raw, _HEURISTIC_CEILING), spans, {
            "branch": "heuristic",
            "total_claims": total,
            "ceiling": _HEURISTIC_CEILING,
            "note": "no judge model configured; surface heuristic only",
        }

    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        context_documents = kwargs.get("context_documents")
        depth = kwargs.get("depth", VerificationDepth.MEDIUM)

        claims = await asyncio.to_thread(_split_claims, output_text)
        if not claims:
            return DetectionResult(
                category=RiskCategory.FACTUALITY, score=0.0, flagged=False,
                branch_used="empty", latency_ms=0.0,
            )

        # Depth decides how much verification this request is worth paying for.
        if depth == VerificationDepth.SHALLOW:
            samples = 0
        elif depth == VerificationDepth.DEEP:
            samples = config.judge_samples
        else:
            samples = max(1, config.judge_samples - 1)

        if context_documents:
            score, spans, details = await asyncio.to_thread(
                self._evidence_branch, output_text, claims, context_documents
            )
        elif samples and judge.judge_configured():
            try:
                score, spans, details = await self._consistency_branch(input_text, output_text, samples)
            except judge.JudgeUnavailable as exc:
                score, spans, details = await asyncio.to_thread(
                    self._heuristic_branch, output_text, claims
                )
                details["judge_error"] = str(exc)
        else:
            score, spans, details = await asyncio.to_thread(
                self._heuristic_branch, output_text, claims
            )

        details["depth"] = depth.value if isinstance(depth, VerificationDepth) else str(depth)

        return DetectionResult(
            category=RiskCategory.FACTUALITY,
            score=score,
            flagged=score >= config.factuality_threshold,
            flagged_spans=spans,
            details=details,
            branch_used=details["branch"],
            latency_ms=(time.time() - start_time) * 1000,
        )
