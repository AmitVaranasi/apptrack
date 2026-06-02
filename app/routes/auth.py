from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.google_oauth import (
    encrypt_token,
    exchange_code,
    get_authorization_url,
    new_oauth_state,
    parse_id_token,
    verify_oauth_state,
)
from app.auth.session import clear_session, get_session_user_id, set_session
from app import db

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

def require_user(request: Request) -> uuid.UUID:
    user_id = get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user_id = get_session_user_id(request)
    if user_id:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "landing.html",
        {"request": request},
    )


@router.get("/auth/google")
async def auth_google():
    state = new_oauth_state()
    url = get_authorization_url(state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str | None = None, state: str | None = None):
    if not code or not state or not verify_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth callback")

    token = await exchange_code(code)
    refresh_token = token.get("refresh_token")
    id_token = token.get("id_token")
    if not refresh_token or not id_token:
        raise HTTPException(status_code=400, detail="Missing tokens from Google")

    claims = parse_id_token(id_token)
    google_sub = claims.get("sub")
    email = claims.get("email")
    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Missing user info from Google")

    user_id = await db.upsert_gmail_account(
        google_sub=google_sub,
        email=email,
        encrypted_refresh_token=encrypt_token(refresh_token),
    )

    response = RedirectResponse(url="/dashboard", status_code=302)
    set_session(response, user_id)
    return response


@router.get("/auth/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    clear_session(response)
    return response
