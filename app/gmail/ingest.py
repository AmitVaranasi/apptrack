from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.auth.google_oauth import decrypt_token, refresh_access_token
from app.classify.base import Classifier
from app.classify.gemini import GeminiClassifier
from app.classify.prefilter import is_likely_job_email
from app import db
from app.gmail.client import (
    HistoryIdTooOldError,
    get_message_metadata,
    get_profile_history_id,
    list_history_message_ids,
    list_message_ids,
)


@dataclass
class SyncResult:
    scanned: int
    candidates: int
    classified: int
    stored: int
    skipped_existing: int
    incremental: bool = False
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


async def _message_ids_for_sync(access_token: str, last_history_id: str | None) -> tuple[list[str], bool]:
    if last_history_id:
        try:
            ids = await list_history_message_ids(access_token, last_history_id)
            return ids, True
        except HistoryIdTooOldError:
            pass
    ids = await list_message_ids(access_token)
    return ids, False


async def _process_messages(
    user_id: uuid.UUID,
    access_token: str,
    message_ids: list[str],
    classifier: Classifier,
) -> SyncResult:
    scanned = len(message_ids)
    candidates = 0
    classified = 0
    stored = 0
    skipped_existing = 0
    errors = 0

    for msg_id in message_ids:
        if await db.email_exists(msg_id):
            skipped_existing += 1
            continue

        meta = await get_message_metadata(access_token, msg_id)
        if not is_likely_job_email(meta.subject, meta.snippet, meta.from_addr):
            continue

        candidates += 1

        try:
            result = await classifier.classify(
                meta.subject,
                meta.from_addr,
                meta.snippet,
            )
        except Exception:
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
        errors=errors,
    )


async def run_sync(user_id: uuid.UUID, classifier: Classifier | None = None) -> SyncResult:
    account = await db.get_gmail_account(user_id)
    if not account:
        raise ValueError("Gmail account not found")

    refresh_token = decrypt_token(account["refresh_token"])
    access_token = await refresh_access_token(refresh_token)

    if classifier is None:
        classifier = GeminiClassifier()

    message_ids, incremental = await _message_ids_for_sync(
        access_token,
        account["last_history_id"],
    )
    result = await _process_messages(user_id, access_token, message_ids, classifier)
    result.incremental = incremental

    history_id = await get_profile_history_id(access_token)
    await db.update_last_history_id(user_id, history_id)
    await db.update_last_synced_at(user_id, datetime.now(timezone.utc))

    return result


async def run_sync_all(classifier: Classifier | None = None) -> list[tuple[uuid.UUID, SyncResult | str]]:
    accounts = await db.list_gmail_accounts()
    results: list[tuple[uuid.UUID, SyncResult | str]] = []

    for account in accounts:
        user_id = account["user_id"]
        try:
            result = await run_sync(user_id, classifier=classifier)
            results.append((user_id, result))
        except Exception as exc:
            results.append((user_id, str(exc)))

    return results
