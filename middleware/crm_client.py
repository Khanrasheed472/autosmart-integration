"""One call to the AutoSmart CRM, POST /api/leads, and what its answer means for us (R4).

Every possible answer is sorted into one of four outcomes:
  created     201: done
  rejected    400, 401, other 4xx: our data or settings are wrong, retrying will not help
  retry       429, 5xx, or the CRM could not be reached: nothing was created, safe to try again
  unconfirmed we sent it but got no answer in time: the lead may exist, do NOT retry blindly
"""
import asyncio
import os
from dataclasses import dataclass

import httpx

transport = None  # tests swap in a fake CRM here
_limit = None     # at most CRM_MAX_CONCURRENT calls to the CRM at the same time


def _limiter() -> asyncio.Semaphore:
    global _limit
    if _limit is None:
        _limit = asyncio.Semaphore(int(os.getenv("CRM_MAX_CONCURRENT", "10")))
    return _limit


@dataclass
class Outcome:
    kind: str
    detail: str = ""
    lead_id: str | None = None
    retry_after: float | None = None


def _retry_after(response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


async def create_lead(lead: dict) -> Outcome:
    url = os.getenv("CRM_URL", "http://127.0.0.1:8001").rstrip("/") + "/api/leads"
    token = os.getenv("CRM_API_TOKEN", "test-token")
    # Longer than the CRM's 10 second worst case, so a slow-but-successful call is seen.
    timeout = float(os.getenv("CRM_TIMEOUT_SECONDS", "15"))
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        # A burst of chats queues here instead of hitting AutoSmart's CRM all at once.
        async with _limiter(), httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(url, json=lead, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # The request never reached the CRM, so nothing can have been created.
        return Outcome("retry", f"CRM unreachable ({exc.__class__.__name__})")
    except httpx.TimeoutException:
        return Outcome("unconfirmed", f"no answer from the CRM within {timeout:.0f}s; the lead may exist")
    except httpx.HTTPError as exc:
        return Outcome("unconfirmed", f"connection broke mid-request ({exc.__class__.__name__}); the lead may exist")

    if response.status_code in (200, 201):
        try:
            lead_id = response.json().get("id")
        except (ValueError, AttributeError):
            lead_id = None
        return Outcome("created", "created", lead_id=lead_id)
    if response.status_code == 429 or response.status_code >= 500:
        return Outcome("retry", f"CRM answered {response.status_code}", retry_after=_retry_after(response))
    return Outcome("rejected", f"CRM answered {response.status_code}: {response.text[:300]}")
