from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pydantic import BaseModel

from app.classify.base import ClassificationResult, Classifier
from app.classify.rate_limit import retry_delay_seconds, wait_for_turn
from app.config import get_settings

SYSTEM_PROMPT = """You classify a single email related to a job application. Output ONLY JSON matching the schema.

Rules:
- stage must be exactly one of: referral, applied, screening, assessment, interview, offer, rejected. NEVER output ghosted.
- If not job-related, set is_job_related=false and leave other fields null.
- confidence < 0.5 means you are unsure.
- via_referral is true if the email indicates a referral."""


class JobClassificationSchema(BaseModel):
    is_job_related: bool
    company: str | None = None
    role: str | None = None
    stage: Literal[
        "referral",
        "applied",
        "screening",
        "assessment",
        "interview",
        "offer",
        "rejected",
    ] | None = None
    via_referral: bool = False
    confidence: float = 0.0


class GeminiClassifier(Classifier):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._min_interval = settings.gemini_min_interval_seconds
        self._max_retries = settings.gemini_max_retries

    async def classify(
        self,
        subject: str,
        from_addr: str,
        snippet: str,
        body: str | None = None,
    ) -> ClassificationResult:
        content = f"From: {from_addr}\nSubject: {subject}\nSnippet: {snippet}"
        if body:
            content += f"\nBody: {body[:3000]}"

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            await wait_for_turn(self._min_interval)
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_json_schema=JobClassificationSchema.model_json_schema(),
                    ),
                )
                return self._parse_response(response.text or "{}")
            except ClientError as exc:
                last_error = exc
                if exc.code == 429 and attempt < self._max_retries - 1:
                    await asyncio.sleep(retry_delay_seconds(exc))
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Classification failed")

    @staticmethod
    def _parse_response(text: str) -> ClassificationResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(match.group()) if match else {"is_job_related": False, "confidence": 0}

        return ClassificationResult(
            is_job_related=bool(data.get("is_job_related")),
            company=data.get("company"),
            role=data.get("role"),
            stage=data.get("stage"),
            via_referral=bool(data.get("via_referral", False)),
            confidence=float(data.get("confidence", 0)),
        )
