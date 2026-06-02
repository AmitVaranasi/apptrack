from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.domain.stages import STAGE_ORDER, STAGE_LABELS, is_ghosted
from app import db
from app.routes.auth import require_user
from app.routes.sync import _format_synced_at, _group_applications

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: uuid.UUID = Depends(require_user)):
    account = await db.get_gmail_account(user_id)
    applications = await db.get_applications(user_id)
    review_emails = await db.get_review_emails(user_id)
    ghosted_days = account["ghosted_after_days"] if account else get_settings().ghosted_after_days
    grouped = _group_applications(applications, ghosted_days)
    last_synced = _format_synced_at(
        account["last_synced_at"] if account else None,
        request.cookies.get("last_synced"),
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "account": account,
            "grouped": grouped,
            "review_emails": review_emails,
            "last_synced": last_synced,
            "stage_labels": STAGE_LABELS,
        },
    )
