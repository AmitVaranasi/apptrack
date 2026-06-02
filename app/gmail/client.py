from __future__ import annotations

import base64
import email.utils
from dataclasses import dataclass
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

from app.config import get_settings


@dataclass
class GmailMessage:
    gmail_message_id: str
    subject: str
    from_addr: str
    snippet: str
    received_at: datetime | None
    body: str | None = None


class HistoryIdTooOldError(Exception):
    pass


def _build_service(access_token: str):
    creds = Credentials(token=access_token)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _decode_body(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])

    for part in payload.get("parts", []):
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            return _decode_body(part["body"]["data"])
        if part.get("parts"):
            nested = _extract_body(part)
            if nested:
                return nested
    return ""


async def get_profile_history_id(access_token: str) -> str:
    service = _build_service(access_token)
    profile = service.users().getProfile(userId="me").execute()
    history_id = profile.get("historyId")
    if not history_id:
        raise RuntimeError("Gmail profile did not return historyId")
    return str(history_id)


async def list_message_ids(access_token: str, days: int | None = None) -> list[str]:
    service = _build_service(access_token)
    days = days or get_settings().gmail_sync_days
    query = f"newer_than:{days}d"

    ids: list[str] = []
    page_token = None
    while True:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute()
        )
        for msg in result.get("messages", []):
            ids.append(msg["id"])
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids


async def list_history_message_ids(access_token: str, start_history_id: str) -> list[str]:
    service = _build_service(access_token)
    ids: set[str] = set()
    page_token = None

    try:
        while True:
            result = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                    maxResults=100,
                )
                .execute()
            )
            for record in result.get("history", []):
                for added in record.get("messagesAdded", []):
                    message = added.get("message", {})
                    if message.get("id"):
                        ids.add(message["id"])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HistoryIdTooOldError from exc
        raise

    return list(ids)


async def get_message_metadata(access_token: str, message_id: str) -> GmailMessage:
    service = _build_service(access_token)
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        )
        .execute()
    )
    headers = msg.get("payload", {}).get("headers", [])
    return GmailMessage(
        gmail_message_id=msg["id"],
        subject=_header(headers, "Subject"),
        from_addr=_header(headers, "From"),
        snippet=msg.get("snippet", ""),
        received_at=_parse_date(_header(headers, "Date")),
    )


async def get_message_full(access_token: str, message_id: str) -> GmailMessage:
    service = _build_service(access_token)
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    headers = msg.get("payload", {}).get("headers", [])
    return GmailMessage(
        gmail_message_id=msg["id"],
        subject=_header(headers, "Subject"),
        from_addr=_header(headers, "From"),
        snippet=msg.get("snippet", ""),
        received_at=_parse_date(_header(headers, "Date")),
        body=_extract_body(msg.get("payload", {})),
    )
