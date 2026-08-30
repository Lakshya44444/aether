import asyncio
from abc import ABC, abstractmethod
from typing import Any

from src.models.schemas import DetectionResult


class BaseDetector(ABC):
    """Abstract base for all Aether detectors.

    `detect` is async but the scans underneath it are regex-bound and never yield.
    Awaiting them directly on the event loop made `asyncio.wait_for` unable to
    interrupt anything -- the policy latency budget was declared and never enforced --
    and let one large request stall every other request in the gateway. Running the
    scan in a worker thread makes the budget real and keeps the loop free.
    """

    @property
    @abstractmethod
    def category(self) -> str:
        """Risk category of this detector."""

    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        # ponytail: the worker thread is not killed when the awaiter is cancelled, so a
        # timeout frees the loop but not the CPU. Input length is capped at the API
        # boundary instead, which bounds the work a single request can start.
        return await asyncio.to_thread(self.scan, input_text, output_text, **kwargs)

    def scan(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        """Synchronous detection body, run off the event loop by `detect`.

        A detector implements either this or `detect`. One that genuinely awaits
        (the factuality judge) overrides `detect` and offloads its own CPU work.
        """
        raise NotImplementedError


def near(text: str, left, right, window: int = 60, ordered: bool = True):
    """Yield (start, end) where a `left` match and a `right` match sit within `window`.

    A literal phrase pattern only catches the wording it was written for. Most of what
    these detectors look for is really a verb next to an object -- a cancel verb near an
    instruction noun, a group term near a generalising frame -- with arbitrary words in
    between. Matching the two halves separately and checking the distance covers the
    paraphrases a single regex misses, without enumerating them.

    `ordered=False` also accepts the object before the verb ("more energy than a
    candidate at that stage of life").
    """
    lefts = [(m.start(), m.end()) for m in left.finditer(text)]
    rights = [(m.start(), m.end()) for m in right.finditer(text)]
    for ls, le in lefts:
        for rs, re_ in rights:
            if 0 <= rs - le <= window:
                yield ls, re_
            elif not ordered and 0 <= ls - re_ <= window:
                yield rs, le
