from __future__ import annotations

import re
from dataclasses import dataclass

ATS_DOMAINS = {
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "ashbyhq.com",
    "icims.com",
    "smartrecruiters.com",
    "jobvite.com",
    "bamboohr.com",
    "workable.com",
    "recruitee.com",
    "breezy.hr",
    "jazz.co",
    "successfactors.com",
    "taleo.net",
    "ultipro.com",
}

SENDER_PREFIXES = ("careers@", "recruiting@", "talent@", "jobs@", "hr@")

JOB_PATTERNS = [
    re.compile(r"appl(y|ied|ication)", re.I),
    re.compile(r"interview", re.I),
    re.compile(r"assessment|coding (test|challenge)", re.I),
    re.compile(r"thank you for your (interest|application)", re.I),
    re.compile(r"unfortunately", re.I),
    re.compile(r"\boffer\b", re.I),
    re.compile(r"referr", re.I),
    re.compile(r"screening", re.I),
    re.compile(r"recruiter", re.I),
    re.compile(r"position", re.I),
]


@dataclass
class EmailCandidate:
    gmail_message_id: str
    subject: str
    snippet: str
    from_addr: str
    from_domain: str


def extract_domain(from_addr: str) -> str:
    match = re.search(r"@([\w.-]+)", from_addr.lower())
    return match.group(1) if match else ""


def is_likely_job_email(subject: str, snippet: str, from_addr: str) -> bool:
    domain = extract_domain(from_addr)
    if domain in ATS_DOMAINS:
        return True
    if any(domain.endswith(d) for d in ATS_DOMAINS):
        return True

    lower_from = from_addr.lower()
    if any(prefix in lower_from for prefix in SENDER_PREFIXES):
        return True
    if "no-reply" in lower_from and "job" in lower_from:
        return True

    text = f"{subject} {snippet}"
    return any(p.search(text) for p in JOB_PATTERNS)
