from datetime import datetime, timedelta, timezone

from app.classify.prefilter import is_likely_job_email
from app.domain.stages import furthest, is_ghosted


def test_furthest_rejected_wins():
    assert furthest("interview", "rejected") == "rejected"
    assert furthest("offer", "rejected") == "rejected"


def test_furthest_progression():
    assert furthest("applied", "interview") == "interview"
    assert furthest("screening", "applied") == "screening"


def test_is_ghosted():
    old = datetime.now(timezone.utc) - timedelta(days=30)
    assert is_ghosted("applied", old, 21) is True
    assert is_ghosted("offer", old, 21) is False
    assert is_ghosted("rejected", old, 21) is False


def test_prefilter_ats_domain():
    assert is_likely_job_email(
        "Application received",
        "Thank you for applying",
        "noreply@greenhouse.io",
    )


def test_prefilter_rejects_random():
    assert not is_likely_job_email(
        "Your Amazon order",
        "Your package has shipped",
        "no-reply@amazon.com",
    )
