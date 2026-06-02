from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.domain.stages import STAGE_ORDER, STAGE_LABELS, is_ghosted
from app import db
from app.routes.auth import require_user

router = APIRouter(tags=["dashboard"])
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


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: uuid.UUID = Depends(require_user)):
    account = await db.get_gmail_account(user_id)
    applications = await db.get_applications(user_id)
    ghosted_days = account["ghosted_after_days"] if account else get_settings().ghosted_after_days
    grouped = _group_applications(applications, ghosted_days)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "account": account,
            "grouped": grouped,
            "last_synced": request.cookies.get("last_synced"),
        },
    )
