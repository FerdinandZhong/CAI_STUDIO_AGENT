from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LayerResult:
    """Returned by every layer check function — uniform interface regardless of layer."""
    blocked: bool
    threat_categories: List[str]
    confidence: float          # 0.0–1.0
    reason: Optional[str] = None


@dataclass
class GuardrailResult:
    """Final tool output — serialised to JSON and returned to the agent."""
    verdict: str               # "ALLOW" | "BLOCK"
    threat_categories: List[str]
    confidence: float
    layer_triggered: Optional[int]   # 0=custom, 1=regex, 2=classifier, 3=domain policy
    reason: Optional[str]
    safe_to_proceed: bool

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "threat_categories": self.threat_categories,
            "confidence": self.confidence,
            "layer_triggered": self.layer_triggered,
            "reason": self.reason,
            "safe_to_proceed": self.safe_to_proceed,
        }
