from __future__ import annotations

from datetime import datetime, timezone

STAGE_ORDER = [
    "referral",
    "applied",
    "screening",
    "assessment",
    "interview",
    "offer",
    "rejected",
]

STAGE_LABELS = {
    "referral": "Referral",
    "applied": "Applied",
    "screening": "Screening",
    "assessment": "Assessment",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
}


def furthest(a: str, b: str) -> str:
    if "rejected" in (a, b):
        return "rejected"
    return max(a, b, key=STAGE_ORDER.index)


def is_ghosted(current_stage: str, last_updated: datetime, ghosted_after_days: int) -> bool:
    if current_stage in ("offer", "rejected"):
        return False
    now = datetime.now(timezone.utc)
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)
    return (now - last_updated).days > ghosted_after_days
