from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.domain.stages import STAGE_ORDER, STAGE_LABELS, is_ghosted
from app.gmail.ingest import run_sync, run_sync_all
from app import db
from app.routes.auth import require_user

router = APIRouter(tags=["sync"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _group_applications(applications, ghosted_after_days: int) -> dict:
    grouped: dict[str, list] = {stage: [] for stage in STAGE_ORDER}
    ghosted: list = []

    for app in applications:
        record = dict(app)
        record["stage_label"] = STAGE_LABELS.get(app["current_stage"], app["current_stage"])
        if is_ghosted(app["current_stage"], app["last_updated"], ghosted_after_days):
            record["is_ghosted"] = True
            ghosted.append(record)
        else:
            record["is_ghosted"] = False
            grouped[app["current_stage"]].append(record)

    return {"stages": grouped, "ghosted": ghosted, "stage_order": STAGE_ORDER, "stage_labels": STAGE_LABELS}


def _format_synced_at(value: datetime | None, cookie_value: str | None = None) -> str | None:
    if value:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return cookie_value


@router.post("/sync", response_class=HTMLResponse)
async def sync_manual(request: Request, user_id: uuid.UUID = Depends(require_user)):
    try:
        result = await run_sync(user_id)
    except Exception as exc:
        message = str(exc)
        if "API key not valid" in message or "API_KEY_INVALID" in message:
            message = (
                "Gemini API key is invalid. Create a new key at "
                "https://aistudio.google.com/apikey, update GEMINI_API_KEY in .env, "
                "then restart the server."
            )
        elif "429" in message or "RESOURCE_EXHAUSTED" in message:
            message = (
                "Gemini rate limit hit. Wait a minute and try Sync again, "
                "or upgrade your Gemini API plan."
            )
        return templates.TemplateResponse(
            request,
            "partials/sync_result.html",
            {
                "request": request,
                "error": message,
                "grouped": _group_applications([], get_settings().ghosted_after_days),
            },
            status_code=200,
        )

    applications = await db.get_applications(user_id)
    account = await db.get_gmail_account(user_id)
    review_emails = await db.get_review_emails(user_id)
    ghosted_days = account["ghosted_after_days"] if account else get_settings().ghosted_after_days
    grouped = _group_applications(applications, ghosted_days)
    synced_at = _format_synced_at(account["last_synced_at"] if account else None)

    response = templates.TemplateResponse(
        request,
        "partials/sync_result.html",
        {
            "request": request,
            "grouped": grouped,
            "review_emails": review_emails,
            "result": result,
            "synced_at": synced_at,
        },
    )
    if synced_at:
        response.set_cookie("last_synced", synced_at, httponly=False, samesite="lax")
    return response


@router.get("/sync/cron")
async def sync_cron(authorization: str | None = Header(default=None)):
    settings = get_settings()
    if not settings.cron_secret:
        raise HTTPException(status_code=403, detail="CRON_SECRET is not configured")

    if authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    results = await run_sync_all()
    summary = []
    for user_id, outcome in results:
        if isinstance(outcome, str):
            summary.append({"user_id": str(user_id), "error": outcome})
        else:
            summary.append(
                {
                    "user_id": str(user_id),
                    "stored": outcome.stored,
                    "scanned": outcome.scanned,
                    "incremental": outcome.incremental,
                    "errors": outcome.errors,
                }
            )

    return JSONResponse({"users": len(summary), "results": summary})
