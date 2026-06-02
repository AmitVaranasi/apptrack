from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "apptrack_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret)


def set_session(response: Response, user_id: uuid.UUID, *, secure: bool | None = None) -> None:
    if secure is None:
        secure = get_settings().google_redirect_uri.startswith("https://")
    token = _serializer().dumps({"user_id": str(user_id)})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=MAX_AGE,
    )


def clear_session(response: Response) -> None:
    secure = get_settings().google_redirect_uri.startswith("https://")
    response.delete_cookie(key=COOKIE_NAME, secure=secure, samesite="lax")


def get_session_user_id(request: Request) -> uuid.UUID | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data: dict[str, Any] = _serializer().loads(token, max_age=MAX_AGE)
        return uuid.UUID(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None
