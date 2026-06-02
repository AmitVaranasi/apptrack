from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Stage = Literal[
    "referral",
    "applied",
    "screening",
    "assessment",
    "interview",
    "offer",
    "rejected",
]


@dataclass
class ClassificationResult:
    is_job_related: bool
    company: str | None
    role: str | None
    stage: Stage | None
    via_referral: bool
    confidence: float


class Classifier(ABC):
    @abstractmethod
    async def classify(
        self,
        subject: str,
        from_addr: str,
        snippet: str,
        body: str | None = None,
    ) -> ClassificationResult:
        pass
