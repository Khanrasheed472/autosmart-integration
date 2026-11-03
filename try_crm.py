"""Poke the fake CRM by hand and see how it behaves.

Start the CRM first in another window:  uvicorn mock_crm.app:app --port 8001
Then run:  python try_crm.py
Watch http://127.0.0.1:8001/leads while it runs.
"""
import time

import httpx

CRM = "http://127.0.0.1:8001"
PASSWORD = {"Authorization": "Bearer test-token"}
LEAD = {
    "full_name": "Aisyah Rahman",
    "phone": "+60123456781",
    "source": "whatsapp",
    "first_message": "Hi, is the new SUV available for a test drive this weekend?",
    "external_ref": "70004321",
    "created_at": "2026-10-01T03:15:42Z",
}


def send(label, body, headers):
    start = time.time()
    r = httpx.post(f"{CRM}/api/leads", json=body, headers=headers, timeout=20)
    retry = f"  (Retry-After: {r.headers['retry-after']}s)" if "retry-after" in r.headers else ""
    print(f"{label:42} -> {r.status_code}  {r.text}{retry}  [{time.time() - start:.1f}s]")


httpx.post(f"{CRM}/mock/reset")
print("Leads emptied. Open http://127.0.0.1:8001/leads to watch.\n")

send("1. No password", LEAD, {})
send("2. Phone in local format (012-345 6783)", {**LEAD, "phone": "012-345 6783"}, PASSWORD)
send("3. Good lead", LEAD, PASSWORD)
send("4. Same lead again (CRM does not check)", LEAD, PASSWORD)
send("5. CRM pretends to be down", LEAD, {**PASSWORD, "X-Mock-Scenario": "error"})
send("6. CRM says slow down", LEAD, {**PASSWORD, "X-Mock-Scenario": "rate_limit"})
print("7. CRM is slow: this one takes about 10 seconds...")
send("   Slow lead (saved before the wait)", LEAD, {**PASSWORD, "X-Mock-Scenario": "slow"})

count = len(httpx.get(f"{CRM}/mock/leads").json())
print(f"\nLeads in the CRM now: {count}. Expected 3 (steps 3, 4 and 7), all highlighted as duplicates.")
