"""Fake AutoSmart CRM for the Qiscus integration test.

It follows Appendix B of the test: POST /api/leads with its validation rules,
plus the CRM's bad habits (slow replies, 503, 429, no duplicate check), so the
middleware can be tested against them. It also serves a leads page at /leads.

Run it:  uvicorn mock_crm.app:app --port 8001
"""
import asyncio
import os
import random
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# Settings, read from environment variables so nothing secret is in the code.
API_TOKEN = os.getenv("CRM_API_TOKEN", "test-token")
SLOW_SECONDS = float(os.getenv("MOCK_SLOW_SECONDS", "10"))
RETRY_AFTER_SECONDS = int(os.getenv("MOCK_RETRY_AFTER", "3"))
RANDOM_FAILURE_RATE = float(os.getenv("MOCK_RANDOM_FAILURE_RATE", "0"))

SOURCES = {"whatsapp", "facebook", "instagram", "other"}
SCENARIOS = {"normal", "slow", "error", "server_error", "rate_limit"}
E164 = re.compile(r"^\+[1-9]\d{6,14}$")  # a plus, then 7 to 15 digits

app = FastAPI(title="Fake AutoSmart CRM")
leads: list[dict] = []    # kept in memory, so restarting the CRM empties it
forced: list[str] = []    # scenarios queued by POST /mock/scenario


def validate(body) -> list[dict]:
    """Return a list of rule breaks. An empty list means the lead is valid."""
    if not isinstance(body, dict):
        return [{"field": "body", "message": "must be a JSON object"}]
    errors = []

    name = body.get("full_name")
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        errors.append({"field": "full_name", "message": "required, 1 to 100 characters"})

    source = body.get("source")
    if source not in SOURCES:
        errors.append({"field": "source", "message": "must be one of whatsapp, facebook, instagram, other"})

    phone = body.get("phone")
    if phone in (None, ""):
        if source == "whatsapp":
            errors.append({"field": "phone", "message": "required when source is whatsapp"})
    elif not isinstance(phone, str) or not E164.match(phone):
        errors.append({"field": "phone", "message": "must be E.164"})

    message = body.get("first_message")
    if message is not None and (not isinstance(message, str) or len(message) > 1000):
        errors.append({"field": "first_message", "message": "max 1000 characters"})

    ref = body.get("external_ref")
    if ref is not None and not isinstance(ref, str):
        errors.append({"field": "external_ref", "message": "must be a string"})

    created = body.get("created_at")
    if created is not None:
        try:
            datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            errors.append({"field": "created_at", "message": "must be ISO 8601"})

    return errors


def pick_scenario(header_value: str | None) -> str:
    """Decide how badly to behave on this request.

    Order: a scenario queued by a test, then the X-Mock-Scenario header,
    then random misbehaviour if switched on, otherwise normal.
    """
    if forced:
        return forced.pop(0)
    if header_value in SCENARIOS:
        return header_value
    if RANDOM_FAILURE_RATE and random.random() < RANDOM_FAILURE_RATE:
        return random.choice(["slow", "error", "server_error", "rate_limit"])
    return "normal"


@app.post("/api/leads")
async def create_lead(
    request: Request,
    authorization: str | None = Header(None),
    content_type: str | None = Header(None),
    x_mock_scenario: str | None = Header(None),
):
    # 1. Password check (401)
    if authorization != f"Bearer {API_TOKEN}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # 2. Misbehave if asked to (503, 500 or 429). Nothing is saved in these cases.
    #    Retry-After comes with 503 and 429, as the spec says.
    scenario = pick_scenario(x_mock_scenario)
    if scenario == "error":
        return JSONResponse(
            {"error": "internal_error"},
            status_code=503,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    if scenario == "server_error":
        return JSONResponse({"error": "internal_error"}, status_code=500)
    if scenario == "rate_limit":
        return JSONResponse(
            {"error": "rate_limited"},
            status_code=429,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    # 3. Rule check (400). The spec lists Content-Type: application/json as a
    #    required header, so a request without it is rejected too.
    if not (content_type or "").startswith("application/json"):
        return JSONResponse(
            {"error": "validation_error",
             "details": [{"field": "Content-Type", "message": "must be application/json"}]},
            status_code=400,
        )
    try:
        body = await request.json()
    except ValueError:
        body = None
    errors = validate(body)
    if errors:
        return JSONResponse({"error": "validation_error", "details": errors}, status_code=400)

    # 4. Save the lead. No duplicate check, on purpose: the real CRM has none.
    lead_id = f"LD-{len(leads) + 1:06d}"
    leads.append({
        "id": lead_id,
        "status": "new",
        "full_name": body["full_name"],
        "phone": body.get("phone") or "",
        "source": body["source"],
        "first_message": body.get("first_message") or "",
        "external_ref": body.get("external_ref") or "",
        "created_at": body.get("created_at") or "",
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # 5. Slow reply AFTER saving, like the real CRM: the lead exists even if
    #    the caller gives up waiting.
    if scenario == "slow":
        await asyncio.sleep(SLOW_SECONDS)

    return JSONResponse({"id": lead_id, "status": "new"}, status_code=201)


# ---- Helpers that exist only in the fake CRM, for testing ----

@app.post("/mock/scenario")
async def queue_scenario(request: Request):
    """Make the next N requests misbehave, e.g. {"scenario": "slow", "count": 1}."""
    body = await request.json()
    scenario = body.get("scenario")
    count = int(body.get("count", 1))
    if scenario not in SCENARIOS:
        return JSONResponse({"error": f"scenario must be one of {sorted(SCENARIOS)}"}, status_code=400)
    forced.extend([scenario] * count)
    return {"queued": list(forced)}


@app.post("/mock/reset")
def reset():
    """Empty the leads and the scenario queue before a demo run."""
    leads.clear()
    forced.clear()
    return {"ok": True}


@app.get("/mock/leads")
def list_leads():
    """All leads as JSON, so the demo script can count them. Not part of the real CRM."""
    return leads


@app.get("/")
def home():
    """The bare address sends you to the leads page."""
    return RedirectResponse("/leads")


@app.get("/leads", response_class=HTMLResponse)
def leads_page():
    """A plain page listing every lead, with duplicates highlighted."""
    ref_counts = Counter(lead["external_ref"] for lead in leads if lead["external_ref"])
    phone_counts = Counter(lead["phone"] for lead in leads if lead["phone"])

    rows = []
    duplicate_rows = 0
    for lead in leads:
        flags = []
        if ref_counts.get(lead["external_ref"], 0) > 1:
            flags.append("same room")
        if phone_counts.get(lead["phone"], 0) > 1:
            flags.append("same phone")
        if flags:
            duplicate_rows += 1
        style = ' style="background:#fde2e1"' if flags else ""
        cells = [lead["id"], lead["full_name"], lead["phone"] or "(none)", lead["source"],
                 lead["external_ref"], lead["first_message"], lead["created_at"],
                 ", ".join(flags) or ""]
        rows.append(f"<tr{style}>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>")

    headers = ["Lead id", "Name", "Phone", "Source", "Room (external ref)",
               "First message", "First contact", "Possible duplicate"]
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>Fake AutoSmart CRM: leads</title>
<style>
body {{ font-family: sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ background: #f2f2f2; }}
</style></head>
<body>
<h1>Fake AutoSmart CRM: leads</h1>
<p>{len(leads)} leads, {duplicate_rows} highlighted as possible duplicates. The page refreshes every 5 seconds.</p>
<table><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr>
{"".join(rows) if rows else '<tr><td colspan="8">No leads yet.</td></tr>'}
</table></body></html>"""
