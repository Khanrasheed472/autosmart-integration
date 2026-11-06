"""What happens to one stored webhook after we have already replied to Qiscus."""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from . import crm_client, store
from .mapping import map_to_lead

log = logging.getLogger("middleware")


def _number(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def process(key: str, allowed=("received",)):
    """Never raises: any surprise is logged and the event is parked as failed (R4)."""
    body = store.claim(key, allowed)
    if body is None:
        return  # finished already, or another worker has it
    try:
        await _process(key, json.loads(body))
    except Exception as exc:  # noqa: BLE001  the service must keep running
        log.exception("event %s: unexpected error, parked as failed", key)
        store.update(key, status="failed", note=f"unexpected error: {exc}")


async def _process(key: str, webhook: dict):
    mapped = map_to_lead(webhook)
    lead, phone = mapped["lead"], mapped["phone"]
    if phone:
        store.update(key, phone_raw=str(phone["raw"]), phone_e164=phone["e164"])

    # 1. Data we cannot send: park it for a person, never drop it.
    if mapped["problem"]:
        store.update(key, status="needs_attention", note=mapped["problem"])
        log.warning("event %s: needs attention, %s", key, mapped["problem"])
        return

    # 2. Returning customer inside the window: no second lead.
    earlier = store.customer_lead(mapped["customer_key"])
    contact = _parse_time(lead.get("created_at"))
    window = timedelta(days=_number("RETURNING_WINDOW_DAYS", "30"))
    if earlier and contact:
        last = _parse_time(earlier["last_lead_at"])
        if last and abs(contact - last) < window:
            store.update(key, status="returning_customer", lead_id=earlier["lead_id"],
                         note=f"same customer had lead {earlier['lead_id']} within {window.days} days")
            log.info("event %s: returning customer, kept lead %s", key, earlier["lead_id"])
            return

    # 3. Call the CRM, retrying only the answers that mean "nothing was created".
    max_attempts = int(_number("CRM_MAX_ATTEMPTS", "4"))
    backoff = _number("CRM_BACKOFF_SECONDS", "1")
    outcome = None
    for attempt in range(1, max_attempts + 1):
        outcome = await crm_client.create_lead(lead)
        store.update(key, attempts=attempt)

        if outcome.kind == "created":
            store.update(key, status="sent", lead_id=outcome.lead_id, note=None)
            store.remember_customer(mapped["customer_key"], outcome.lead_id,
                                    lead.get("created_at") or store.now())
            log.info("event %s: lead %s created (attempt %d)", key, outcome.lead_id, attempt)
            return
        if outcome.kind == "rejected":
            store.update(key, status="needs_attention", note=outcome.detail)
            log.error("event %s: CRM rejected the lead, not retrying: %s", key, outcome.detail)
            return
        if outcome.kind == "unconfirmed":
            store.update(key, status="unconfirmed", note=outcome.detail)
            log.error("event %s: %s; check the CRM before replaying", key, outcome.detail)
            return

        if attempt < max_attempts:
            wait = outcome.retry_after if outcome.retry_after is not None else backoff * 2 ** (attempt - 1)
            wait = min(wait, 30)
            log.warning("event %s: %s (attempt %d/%d), retrying in %.0fs",
                        key, outcome.detail, attempt, max_attempts, wait)
            await asyncio.sleep(wait)

    store.update(key, status="failed", note=f"gave up after {max_attempts} attempts: {outcome.detail}")
    log.error("event %s: gave up after %d attempts (%s); kept for replay", key, max_attempts, outcome.detail)


def recover() -> list[str]:
    """Run at start-up, after a crash or restart.

    'processing' means we stopped in the middle of a CRM call, so the lead may
    or may not exist: mark it unconfirmed for a person to check. 'received'
    means we never started, so it is safe to process now.
    """
    for key in store.keys_with_status("processing"):
        store.update(key, status="unconfirmed",
                     note="service stopped while this event was in progress; check the CRM before replaying")
        log.warning("event %s: was in progress at shutdown, marked unconfirmed", key)
    return store.keys_with_status("received")
