"""The integration middleware: receives Qiscus New Session webhooks, creates CRM leads.

Run it:  uvicorn middleware.main:app --port 8000
Qiscus would be pointed at:  POST http://<host>:8000/webhooks/qiscus
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from html import escape

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import processor, store
from .mapping import WebhookError, customer_key, dedupe_key

log = logging.getLogger("middleware")
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

_tasks: set = set()


@asynccontextmanager
async def lifespan(app):
    store.init()
    for key in processor.recover():  # webhooks stored but never processed before a restart
        task = asyncio.create_task(processor.process(key))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    yield


app = FastAPI(title="AutoSmart integration middleware", lifespan=lifespan)


@app.post("/webhooks/qiscus")
async def receive_webhook(request: Request, background: BackgroundTasks):
    """Store first, reply at once, call the CRM afterwards (Qiscus waits only 5 seconds)."""
    raw = await request.body()
    try:
        webhook = json.loads(raw)
        if isinstance(webhook, dict) and webhook.get("webhook_type") not in (None, "NEW_SESSION_WEBHOOK"):
            return {"status": "ignored", "reason": "not a NEW_SESSION_WEBHOOK"}
        key = dedupe_key(webhook)
        ckey = customer_key(webhook)
    except (ValueError, WebhookError) as exc:
        log.warning("rejected a webhook that cannot be used: %s", exc)
        return JSONResponse({"error": "bad_webhook", "detail": str(exc)}, status_code=400)

    try:
        # On a side thread, so a busy logbook never holds up the replies to Qiscus.
        is_new = await asyncio.to_thread(store.add_event, key, ckey, raw.decode("utf-8"))
    except Exception:  # noqa: BLE001  could not save it: ask Qiscus to send it again
        log.exception("could not store webhook %s", key)
        return JSONResponse({"error": "storage_unavailable"}, status_code=503)

    if not is_new:
        log.info("event %s: duplicate delivery ignored", key)
        return {"status": "duplicate", "key": key}

    log.info("event %s: received, replying to Qiscus now", key)
    background.add_task(processor.process, key)
    return {"status": "accepted", "key": key}


@app.get("/")
def home():
    """The bare address opens the review page."""
    return RedirectResponse("/review")


@app.get("/health")
def health():
    return {"ok": True}


# ---- For the people looking after the integration ----

@app.get("/admin/events")
def list_events(status: str | None = None):
    """All stored webhooks, or only one status, e.g. ?status=needs_attention"""
    return [{k: v for k, v in e.items() if k != "body"} for e in store.events(status)]


@app.get("/admin/events/{key}")
def one_event(key: str):
    event = store.get(key)
    if not event:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {k: v for k, v in event.items() if k != "body"}


@app.post("/admin/replay")
def replay(background: BackgroundTasks, status: str = "failed", key: str | None = None):
    """Send parked events again: all 'failed' ones, or a single key after a person has checked it."""
    keys = [key] if key else store.keys_with_status(status)
    for k in keys:
        background.add_task(processor.process, k, ("failed", "needs_attention", "unconfirmed"))
    return {"replaying": keys}


@app.post("/admin/reset")
def reset():
    """Demo only: empty the logbook. Switch off with ALLOW_ADMIN_RESET=0."""
    if os.getenv("ALLOW_ADMIN_RESET", "1") != "1":
        return JSONResponse({"error": "disabled"}, status_code=403)
    store.reset()
    return {"ok": True}


STATUS_COLOURS = {"sent": "#e3f4ea", "returning_customer": "#eef0f7", "needs_attention": "#fdf1d8",
                  "unconfirmed": "#fdf1d8", "failed": "#fde2e1"}


@app.get("/review", response_class=HTMLResponse)
def review_page():
    """Every webhook and what happened to it; anything not 'sent' needs a look."""
    rows = []
    for e in store.events():
        colour = STATUS_COLOURS.get(e["status"], "#ffffff")
        cells = [e["received_at"], e["dedupe_key"], e["status"], e["lead_id"] or "", e["attempts"],
                 e["phone_raw"] or "", e["phone_e164"] or "", e["note"] or ""]
        rows.append(f'<tr style="background:{colour}">' + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>")
    headers = ["Received", "Event (room:message)", "Status", "CRM lead", "Attempts", "Phone as received",
               "Phone sent", "Note"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>Integration review</title><style>body{{font-family:sans-serif;margin:24px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #ccc;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f2f2f2}}</style></head><body><h1>Integration review</h1>
<p>Green = lead created. Amber = a person should check. Red = gave up, replay with POST /admin/replay.</p>
<table><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr>
{"".join(rows) or '<tr><td colspan="8">No webhooks yet.</td></tr>'}</table></body></html>"""
