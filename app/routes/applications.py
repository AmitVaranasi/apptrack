from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.domain.stages import STAGE_ORDER, STAGE_LABELS, is_ghosted
from app import db
from app.routes.auth import require_user
from app.templating import templates

router = APIRouter(tags=["applications"])


@router.post("/review/{email_id}/dismiss", response_class=HTMLResponse)
async def dismiss_review(
    request: Request,
    email_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user),
):
    await db.dismiss_email_review(user_id, email_id)
    review_emails = await db.get_review_emails(user_id)
    return templates.TemplateResponse(
        request,
        "partials/review_queue.html",
        {
            "request": request,
            "review_emails": review_emails,
            "stage_labels": STAGE_LABELS,
        },
    )


@router.get("/app/{app_id}", response_class=HTMLResponse)
async def application_detail(
    request: Request,
    app_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user),
):
    app = await db.get_application(user_id, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    emails = await db.get_application_emails(app_id)
    account = await db.get_gmail_account(user_id)
    ghosted_days = account["ghosted_after_days"] if account else get_settings().ghosted_after_days

    return templates.TemplateResponse(
        request,
        "partials/detail.html",
        {
            "request": request,
            "app": app,
            "emails": emails,
            "stage_labels": STAGE_LABELS,
            "is_ghosted": is_ghosted(app["current_stage"], app["last_updated"], ghosted_days),
        },
    )


@router.get("/app/{app_id}/edit", response_class=HTMLResponse)
async def application_edit_form(
    request: Request,
    app_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user),
):
    app = await db.get_application(user_id, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    duplicates = await db.find_duplicate_candidates(user_id, app["company"], exclude_id=app_id)

    return templates.TemplateResponse(
        request,
        "partials/edit.html",
        {
            "request": request,
            "app": app,
            "stage_order": STAGE_ORDER,
            "stage_labels": STAGE_LABELS,
            "duplicates": duplicates,
        },
    )


@router.post("/app/{app_id}", response_class=HTMLResponse)
async def application_update(
    request: Request,
    app_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user),
    company: str = Form(...),
    role: str = Form(""),
    current_stage: str = Form(...),
    via_referral: str = Form("false"),
    notes: str = Form(""),
):
    app = await db.get_application(user_id, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    if current_stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid stage")

    await db.update_application(
        user_id,
        app_id,
        company=company.strip(),
        role=role.strip() or None,
        current_stage=current_stage,
        via_referral=via_referral.lower() in ("true", "1", "on", "yes"),
        notes=notes.strip() or None,
    )

    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/app/{app_id}/merge", response_class=HTMLResponse)
async def application_merge(
    request: Request,
    app_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user),
    source_id: uuid.UUID = Form(...),
):
    try:
        await db.merge_applications(user_id, app_id, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(url=f"/app/{app_id}", status_code=303)
