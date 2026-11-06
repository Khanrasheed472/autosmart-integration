"""Proof it works (R5): plays Qiscus against the middleware and the fake CRM.

Start both first, each in its own window:
    uvicorn mock_crm.app:app --port 8001
    uvicorn middleware.main:app --port 8000
Then run:
    python demo.py
Watch http://127.0.0.1:8001/leads (CRM) and http://127.0.0.1:8000/review (middleware) while it runs.
Takes about a minute, because the CRM is made slow and broken on purpose.
"""
import sys
import threading
import time

import httpx

from tests.samples import APPENDIX_A, APPENDIX_B_EXAMPLE, FILE_MESSAGE, make_webhook

MIDDLEWARE = "http://127.0.0.1:8000"
CRM = "http://127.0.0.1:8001"
results = []


def check(rule, label, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {rule:4} {label}" + (f"   [{detail}]" if detail else ""))


def send(webhook):
    start = time.time()
    r = httpx.post(f"{MIDDLEWARE}/webhooks/qiscus", json=webhook, timeout=10)
    return r.json(), time.time() - start


def final(key, timeout=60):
    """Wait until the middleware has finished with an event, then return it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        event = httpx.get(f"{MIDDLEWARE}/admin/events/{key}").json()
        if event.get("status") not in ("received", "processing"):
            return event
        time.sleep(0.5)
    return event


def leads_for(webhook):
    ref = webhook["room_log"]["room_id"]
    return [lead for lead in httpx.get(f"{CRM}/mock/leads").json() if lead["external_ref"] == ref]


def crm_misbehaves(scenario, count=1):
    httpx.post(f"{CRM}/mock/scenario", json={"scenario": scenario, "count": count})


def phone(n):
    return f"6012345{6800 + n}"   # a different valid Malaysian number for each customer


def main():
    try:
        httpx.post(f"{CRM}/mock/reset").raise_for_status()
        httpx.post(f"{MIDDLEWARE}/admin/reset").raise_for_status()
    except httpx.HTTPError:
        sys.exit("Start the CRM (port 8001) and the middleware (port 8000) first. See the top of this file.")
    print("CRM and middleware emptied.\n")

    print("R1  Field mapping")
    reply, _ = send(APPENDIX_A)
    event = final(reply["key"])
    lead = leads_for(APPENDIX_A)
    sent = {k: lead[0][k] for k in APPENDIX_B_EXAMPLE} if lead else {}
    check("R1", "Appendix A sample becomes exactly the Appendix B example request", sent == APPENDIX_B_EXAMPLE,
          f"lead {event.get('lead_id')}")

    w = make_webhook(1, source="ig", user_id="ig-5550001", name="Mei Ling", message=FILE_MESSAGE)
    final(send(w)[0]["key"])
    lead = leads_for(w)
    check("R1", "Instagram photo: caption used as first message, no phone sent",
          bool(lead) and lead[0]["first_message"] == FILE_MESSAGE["payload"]["caption"]
          and lead[0]["source"] == "instagram" and lead[0]["phone"] == "")

    w = make_webhook(2, source="fb", user_id="2345678901234567", name="Raj Kumar",
                     message={"type": "text", "text": "Any promotions on the sedan?", "payload": {}})
    final(send(w)[0]["key"])
    lead = leads_for(w)
    check("R1", "Facebook: source is facebook, the platform id is not sent as a phone",
          bool(lead) and lead[0]["source"] == "facebook" and lead[0]["phone"] == "")

    print("\nR2  Phone numbers")
    w = make_webhook(3, user_id="012-345 6783", name="Local Format")
    final(send(w)[0]["key"])
    lead = leads_for(w)
    check("R2", "012-345 6783 is sent as +60123456783", bool(lead) and lead[0]["phone"] == "+60123456783")

    w = make_webhook(4, user_id="+65 9123 4566", name="Singapore Customer")
    final(send(w)[0]["key"])
    lead = leads_for(w)
    check("R2", "+65 9123 4566 is sent as +6591234566", bool(lead) and lead[0]["phone"] == "+6591234566")

    for n, bad in ((5, "12345"), (6, "0812 0000 0009")):
        w = make_webhook(n, user_id=bad, name="Unclear Number")
        event = final(send(w)[0]["key"])
        check("R2", f"WhatsApp {bad!r} is parked for a person, no lead created",
              event["status"] == "needs_attention" and not leads_for(w), event.get("note", ""))

    print("\nR3  No duplicate leads")
    w = make_webhook(7, user_id=phone(7), name="Twice Sent")
    first, second = send(w)[0], send(w)[0]
    final(first["key"])
    check("R3", "Same webhook twice: one lead", len(leads_for(w)) == 1 and second["status"] == "duplicate")

    w = make_webhook(8, user_id=phone(8), name="Five At Once")
    threads = [threading.Thread(target=send, args=(w,)) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    final(f"{w['payload']['room']['id_str']}:{w['payload']['message']['id_str']}")
    check("R3", "Same webhook 5 times at the same instant: one lead", len(leads_for(w)) == 1)

    print("\nR4  CRM errors (this part takes about 30 seconds)")
    w = make_webhook(9, user_id=phone(9), name="Slow CRM")
    crm_misbehaves("slow")
    reply, seconds = send(w)
    event = final(reply["key"])
    check("R4", "CRM takes 10s: Qiscus still gets its answer well inside 5s, one lead",
          seconds < 5 and event["status"] == "sent" and len(leads_for(w)) == 1, f"answered in {seconds:.2f}s")

    w = make_webhook(10, user_id=phone(10), name="CRM 503")
    crm_misbehaves("error")
    event = final(send(w)[0]["key"])
    check("R4", "CRM answers 503 once: retried, one lead",
          event["status"] == "sent" and event["attempts"] == 2 and len(leads_for(w)) == 1)

    w = make_webhook(11, user_id=phone(11), name="CRM 429")
    crm_misbehaves("rate_limit")
    event = final(send(w)[0]["key"])
    check("R4", "CRM answers 429: waited Retry-After, retried, one lead",
          event["status"] == "sent" and event["attempts"] == 2 and len(leads_for(w)) == 1)

    w = make_webhook(12, user_id=phone(12), name="CRM Down")
    crm_misbehaves("server_error", 4)
    event = final(send(w)[0]["key"])
    healthy = httpx.get(f"{MIDDLEWARE}/health").status_code == 200
    check("R4", "CRM fails 4 times: parked as failed, service still running, no lead",
          event["status"] == "failed" and healthy and not leads_for(w))
    httpx.post(f"{MIDDLEWARE}/admin/replay", params={"key": event["dedupe_key"]})
    time.sleep(0.5)
    event = final(event["dedupe_key"])
    check("B1", "Replayed after the CRM recovered: one lead", event["status"] == "sent" and len(leads_for(w)) == 1)

    print("\n3.3 Returning customer (30 day window)")
    first = make_webhook(13, user_id=phone(13), name="Returning", timestamp="2026-10-01T03:00:00Z")
    final(send(first)[0]["key"])
    again = make_webhook(14, user_id=phone(13), name="Returning", timestamp="2026-10-15T03:00:00Z")
    event = final(send(again)[0]["key"])
    check("3.3", "Back after 2 weeks: no new lead, linked to the first one",
          event["status"] == "returning_customer" and not leads_for(again), f"linked to {event.get('lead_id')}")
    later = make_webhook(15, user_id=phone(13), name="Returning", timestamp="2026-11-20T03:00:00Z")
    event = final(send(later)[0]["key"])
    check("3.3", "Back after 7 weeks: a new lead", event["status"] == "sent" and len(leads_for(later)) == 1)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed.")
    print(f"Leads:  {CRM}/leads\nReview: {MIDDLEWARE}/review")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
