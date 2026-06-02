from __future__ import annotations

import re


def normalize_company(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def normalize_role(role: str | None) -> str | None:
    if not role:
        return None
    return re.sub(r"\s+", " ", role.strip())


def companies_match(a: str, b: str) -> bool:
    return normalize_company(a) == normalize_company(b)
