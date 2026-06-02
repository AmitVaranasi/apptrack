from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from google.genai.errors import ClientError

from app.auth.google_oauth import decrypt_token, refresh_access_token
from app.classify.base import Classifier
from app.classify.gemini import GeminiClassifier
from app.classify.prefilter import is_likely_job_email
from app.config import get_settings
from app import db
from app.gmail.client import get_message_metadata, list_message_ids


@dataclass
class SyncResult:
    scanned: int
    candidates: int
    classified: int
    stored: int
    skipped_existing: int
    stopped_early: bool = False
    errors: int = 0


async def _store_classification(
    user_id: uuid.UUID,
    meta,
    result,
) -> None:
    email_date = meta.received_at or datetime.now(timezone.utc)
    needs_review = result.confidence < 0.5 or not result.company
    app_id = await db.upsert_application_from_email(
        user_id=user_id,
        company=result.company or "Unknown",
        role=result.role,
        detected_stage=result.stage or "applied",
        via_referral=result.via_referral,
        email_date=email_date,
    )
    await db.insert_email(
        application_id=app_id,
        gmail_message_id=meta.gmail_message_id,
        subject=meta.subject,
        from_addr=meta.from_addr,
        received_at=meta.received_at,
        detected_stage=result.stage,
        confidence=result.confidence,
        needs_review=needs_review,
    )


async def run_sync(user_id: uuid.UUID, classifier: Classifier | None = None) -> SyncResult:
    account = await db.get_gmail_account(user_id)
    if not account:
        raise ValueError("Gmail account not found")

    settings = get_settings()
    refresh_token = decrypt_token(account["refresh_token"])
    access_token = await refresh_access_token(refresh_token)

    if classifier is None:
        classifier = GeminiClassifier()

    message_ids = await list_message_ids(access_token)
    scanned = len(message_ids)
    candidates = 0
    classified = 0
    stored = 0
    skipped_existing = 0
    stopped_early = False
    errors = 0
    gemini_calls = 0
    max_calls = settings.gemini_max_calls_per_sync

    for msg_id in message_ids:
        if await db.email_exists(msg_id):
            skipped_existing += 1
            continue

        meta = await get_message_metadata(access_token, msg_id)
        if not is_likely_job_email(meta.subject, meta.snippet, meta.from_addr):
            continue

        candidates += 1

        if gemini_calls >= max_calls:
            stopped_early = True
            break

        try:
            result = await classifier.classify(
                meta.subject,
                meta.from_addr,
                meta.snippet,
            )
            gemini_calls += 1
        except ClientError as exc:
            gemini_calls += 1
            if exc.code == 429:
                stopped_early = True
                errors += 1
                break
            errors += 1
            continue
        except Exception:
            gemini_calls += 1
            errors += 1
            continue

        if not result.is_job_related:
            continue

        classified += 1
        await _store_classification(user_id, meta, result)
        stored += 1

    return SyncResult(
        scanned=scanned,
        candidates=candidates,
        classified=classified,
        stored=stored,
        skipped_existing=skipped_existing,
        stopped_early=stopped_early,
        errors=errors,
    )
