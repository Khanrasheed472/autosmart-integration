"""R3 and R4 against a fake CRM that lives inside the test, so nothing else needs to run."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from middleware import crm_client, processor, store
from middleware.main import app
from tests.samples import APPENDIX_A, make_webhook


class FakeCRM:
    """Answers each call with the next scripted reply, and counts calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, request: httpx.Request):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else 201
        if isinstance(reply, Exception):
            raise reply
        if reply == "timeout":
            raise httpx.ReadTimeout("no answer", request=request)
        if reply == "down":
            raise httpx.ConnectError("refused", request=request)
        body = {"id": f"LD-{self.calls:06d}", "status": "new"} if reply == 201 else {"error": "x"}
        return httpx.Response(reply, json=body)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDDLEWARE_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("CRM_BACKOFF_SECONDS", "0")

    def use(*replies):
        crm = FakeCRM(*replies)
        monkeypatch.setattr(crm_client, "transport", httpx.MockTransport(crm))
        return crm
    return use


def test_same_webhook_twice_creates_one_lead(setup):
    crm = setup(201)
    with TestClient(app) as client:
        first = client.post("/webhooks/qiscus", json=APPENDIX_A)
        second = client.post("/webhooks/qiscus", json=APPENDIX_A)
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert crm.calls == 1


def test_503_is_retried_then_succeeds(setup):
    crm = setup(503, 201)
    with TestClient(app) as client:
        key = client.post("/webhooks/qiscus", json=make_webhook(1)).json()["key"]
        event = client.get(f"/admin/events/{key}").json()
    assert crm.calls == 2 and event["status"] == "sent" and event["attempts"] == 2


def test_timeout_is_unconfirmed_and_not_retried(setup):
    crm = setup("timeout")
    with TestClient(app) as client:
        key = client.post("/webhooks/qiscus", json=make_webhook(2)).json()["key"]
        event = client.get(f"/admin/events/{key}").json()
    assert crm.calls == 1 and event["status"] == "unconfirmed"


def test_crm_down_is_retried_then_parked_and_service_stays_up(setup):
    crm = setup("down", "down", "down", "down")
    with TestClient(app) as client:
        key = client.post("/webhooks/qiscus", json=make_webhook(3)).json()["key"]
        event = client.get(f"/admin/events/{key}").json()
        healthy = client.get("/health").status_code
    assert crm.calls == 4 and event["status"] == "failed" and healthy == 200


def test_400_from_crm_is_not_retried(setup):
    crm = setup(400)
    with TestClient(app) as client:
        key = client.post("/webhooks/qiscus", json=make_webhook(4)).json()["key"]
        event = client.get(f"/admin/events/{key}").json()
    assert crm.calls == 1 and event["status"] == "needs_attention"


def test_bad_whatsapp_phone_never_reaches_the_crm(setup):
    crm = setup()
    with TestClient(app) as client:
        key = client.post("/webhooks/qiscus", json=make_webhook(5, user_id="12345")).json()["key"]
        event = client.get(f"/admin/events/{key}").json()
    assert crm.calls == 0 and event["status"] == "needs_attention" and event["phone_raw"] == "12345"


def test_returning_customer_inside_window_gets_no_second_lead(setup):
    crm = setup(201, 201)
    with TestClient(app) as client:
        client.post("/webhooks/qiscus", json=make_webhook(6, user_id="60123456790"))
        k2 = client.post("/webhooks/qiscus", json=make_webhook(7, user_id="60123456790",
                                                               timestamp="2026-10-15T03:00:00Z")).json()["key"]
        k3 = client.post("/webhooks/qiscus", json=make_webhook(8, user_id="60123456790",
                                                               timestamp="2026-11-20T03:00:00Z")).json()["key"]
        s2 = client.get(f"/admin/events/{k2}").json()["status"]
        s3 = client.get(f"/admin/events/{k3}").json()["status"]
    assert s2 == "returning_customer" and s3 == "sent" and crm.calls == 2


def test_garbage_body_gets_400(setup):
    setup()
    with TestClient(app) as client:
        assert client.post("/webhooks/qiscus", content=b"not json").status_code == 400


def test_event_in_progress_at_a_crash_becomes_unconfirmed(setup):
    setup()
    store.init()
    store.add_event("1:1", "wa:x", json.dumps(APPENDIX_A))
    store.claim("1:1")                     # it was mid-way when the service stopped
    processor.recover()
    assert store.get("1:1")["status"] == "unconfirmed"
