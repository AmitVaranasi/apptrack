from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates


def gmail_link(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates"),
)
templates.env.globals["gmail_link"] = gmail_link
