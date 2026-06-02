from __future__ import annotations

import asyncio
import re
import time

_lock = asyncio.Lock()
_last_call_at = 0.0


async def wait_for_turn(min_interval_seconds: float) -> None:
    global _last_call_at
    async with _lock:
        now = time.monotonic()
        wait = _last_call_at + min_interval_seconds - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


def retry_delay_seconds(error: Exception, default: float = 15.0) -> float:
    match = re.search(r"retry in ([\d.]+)s", str(error), re.I)
    if match:
        return float(match.group(1)) + 1.0
    return default
