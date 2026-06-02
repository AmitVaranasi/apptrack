from __future__ import annotations

import base64
import json
import uuid
from functools import lru_cache

from authlib.integrations.httpx_client import AsyncOAuth2Client
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail="Invalid stored token") from exc


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()


@lru_cache
def _oauth_client() -> AsyncOAuth2Client:
    settings = get_settings()
    return AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
        scope=" ".join(settings.google_scopes),
    )


def get_authorization_url(state: str) -> str:
    client = _oauth_client()
    uri, _ = client.create_authorization_url(
        "https://accounts.google.com/o/oauth2/v2/auth",
        state=state,
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return uri


async def exchange_code(code: str) -> dict:
    client = _oauth_client()
    token = await client.fetch_token(
        "https://oauth2.googleapis.com/token",
        code=code,
        grant_type="authorization_code",
    )
    return token


def parse_id_token(id_token: str) -> dict:
    """Decode JWT payload without signature verification (token came from Google directly)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Invalid id_token")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload + padding)
    return json.loads(decoded)


async def refresh_access_token(refresh_token: str) -> str:
    client = _oauth_client()
    token = await client.refresh_token(
        "https://oauth2.googleapis.com/token",
        refresh_token=refresh_token,
    )
    access_token = token.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Failed to refresh access token")
    return access_token


def new_oauth_state() -> str:
    return _serializer().dumps({"nonce": uuid.uuid4().hex})


def verify_oauth_state(state: str) -> bool:
    try:
        _serializer().loads(state, max_age=600)
        return True
    except Exception:
        return False


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    from app.config import get_settings

    return URLSafeTimedSerializer(get_settings().session_secret)
