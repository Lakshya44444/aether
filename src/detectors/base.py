from abc import ABC, abstractmethod
from typing import Any
from src.models.schemas import DetectionResult

class BaseDetector(ABC):
    """Abstract base for all Sentinel detectors."""
    
    @abstractmethod
    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        """Run detection on the interaction."""
        pass
    
    @property
    @abstractmethod
    def category(self) -> str:
        """Risk category of this detector."""
        pass
