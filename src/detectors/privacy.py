import ipaddress
import re
import time
from typing import Any

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector
from src.config import config

# Severity per PII type. A leaked national identifier is not the same event as a leaked
# office phone number, so the score is driven by what was found rather than how many
# matches happened to land in the text.
_SEVERITY = {
    "SSN": 1.0,
    "CREDIT_CARD": 1.0,
    "API_KEY": 1.0,
    "IBAN": 1.0,
    "BANK_ACCOUNT": 1.0,
    "NATIONAL_ID": 1.0,
    "MEDICAL_RECORD": 1.0,
    "PASSPORT": 1.0,
    "ADDRESS": 0.5,
    "EMAIL": 0.4,
    "PHONE": 0.4,
    "IP_ADDRESS": 0.3,
}

_PATTERNS = {
    "EMAIL": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    "PHONE": r'\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
    "SSN": r'\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b',
    # Vendors segment their keys (sk-proj-..., sk-ant-...), so the old
    # `sk-[a-zA-Z0-9]{20,}` stopped at the first hyphen and matched nothing.
    "API_KEY": r'\b(?:sk|pk|rk|api|key|token)[-_][A-Za-z0-9][A-Za-z0-9\-_]{18,}\b|\bAKIA[0-9A-Z]{16}\b',
    # Identifiers that only mean anything next to their own label. Matching the bare
    # digits would flag every order number in the corpus.
    "MEDICAL_RECORD": r'\b(?i:MRN|medical\s+record\s+(?:number|no\.?))\s*:?\s*#?\s*\d{5,12}\b',
    "PASSPORT": r'\b(?i:passport\s+(?:number|no\.?))\s*:?\s*[A-Z0-9]{6,9}\b',
    "BANK_ACCOUNT": r'\b(?i:sort\s+code)\s*:?\s*\d{2}[-\s]?\d{2}[-\s]?\d{2}\b|'
                    r'\b(?i:account\s+number)\s*:?\s*\d{6,12}\b',
    # ISO 13616: two letters, two check digits, then up to 30 alphanumerics in groups.
    "IBAN": r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b',
    # UK National Insurance number: two letters, three digit pairs, one suffix letter.
    # The grouping is what makes it distinctive; restricting the leading letters to the
    # set real numbers use would reject QQ 12 34 56 C, the example HMRC publishes.
    "NATIONAL_ID": r'\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b',
    "IP_ADDRESS": r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b',
    "CREDIT_CARD": r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
}

# Street suffixes are matched as whole tokens. Alternating a bare "St" under IGNORECASE
# previously matched the tail of last/most/must/past/cost, turning "3 nodes last night"
# into a street address.
_ADDRESS_PATTERN = (
    r'\b\d{1,6}\s+(?:[A-Z][A-Za-z]*\s+){1,4}'
    r'(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b\.?'
)

# A dotted quad written right after a version word is a release number, not a host.
_VERSION_CONTEXT = re.compile(r'(?:\bv|\bversion|\brelease|\bbuild)\s*$', re.IGNORECASE)


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

    @staticmethod
    def _is_iban(candidate: str) -> bool:
        """ISO 7064 mod-97 check.

        The shape alone matches ordinary uppercase text like a ticket reference, so the
        checksum is what separates an account number from a coincidence.
        """
        raw = candidate.replace(" ", "")
        if not (15 <= len(raw) <= 34):
            return False
        rearranged = raw[4:] + raw[:4]
        digits = ""
        for char in rearranged:
            if char.isdigit():
                digits += char
            elif char.isalpha():
                digits += str(ord(char.upper()) - 55)
            else:
                return False
        return int(digits) % 97 == 1

    def _is_reportable_ip(self, text: str, match: re.Match) -> bool:
        """Rejects malformed quads, version strings, and private/reserved ranges.

        An RFC1918 address in an internal copilot's output is infrastructure detail,
        not personal data, and flagging it is the main source of privacy false alarms.
        """
        try:
            addr = ipaddress.ip_address(match.group())
        except ValueError:
            return False
        if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
            return False
        return not _VERSION_CONTEXT.search(text[: match.start()])

    def scan(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        text_to_check = output_text

        flagged_spans = []
        found_types = set()

        def add(pii_type: str, match: re.Match) -> None:
            found_types.add(pii_type)
            flagged_spans.append(FlaggedSpan(
                start=match.start(),
                end=match.end(),
                text=match.group(),
                categories=[RiskCategory.PRIVACY],
                severity=_SEVERITY[pii_type],
                detail=f"Found PII of type {pii_type}"
            ))

        for pii_type, pattern in _PATTERNS.items():
            for match in re.finditer(pattern, text_to_check):
                if pii_type == "CREDIT_CARD" and not self._luhn_check(match.group()):
                    continue
                if pii_type == "IP_ADDRESS" and not self._is_reportable_ip(text_to_check, match):
                    continue
                if pii_type == "IBAN" and not self._is_iban(match.group()):
                    continue
                add(pii_type, match)

        for match in re.finditer(_ADDRESS_PATTERN, text_to_check):
            add("ADDRESS", match)

        # The worst thing found sets the score. Additional distinct types raise it
        # modestly, because a message leaking three kinds of identifier is worse than
        # one leaking a single kind, but never as much as the type itself matters.
        if flagged_spans:
            score = max(span.severity for span in flagged_spans)
            score = min(1.0, score + 0.1 * (len(found_types) - 1))
        else:
            score = 0.0

        latency_ms = (time.time() - start_time) * 1000

        return DetectionResult(
            category=RiskCategory.PRIVACY,
            score=score,
            flagged=score >= config.privacy_threshold,
            flagged_spans=flagged_spans,
            details={"found_types": sorted(found_types), "count": len(flagged_spans)},
            latency_ms=latency_ms
        )
