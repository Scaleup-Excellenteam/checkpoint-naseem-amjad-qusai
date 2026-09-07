"""Shared security evaluation result; independent of transport and delivery."""

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class SecurityDecision:
    decision: Decision
    reason: str | None = None
    verdict: str | None = None

    def __post_init__(self):
        if not isinstance(self.decision, Decision):
            raise TypeError("decision must be a Decision enum member")
