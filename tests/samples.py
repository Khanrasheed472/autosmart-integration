"""Webhook bodies from Appendix A, reused by the tests and demo.py."""
import copy

APPENDIX_A = {
    "webhook_type": "NEW_SESSION_WEBHOOK",
    "is_new_session": True,
    "payload": {
        "from": {
            "id": 90001234,
            "id_str": "90001234",
            "name": "Aisyah Rahman",
            "email": "60123456781",
            "avatar_url": "https://example.com/avatar/default.png",
        },
        "message": {
            "id": 550001001,
            "id_str": "550001001",
            "type": "text",
            "text": "Hi, is the new SUV available for a test drive this weekend?",
            "payload": {},
            "timestamp": "2026-10-01T03:15:42Z",
        },
        "room": {
            "id": 70004321,
            "id_str": "70004321",
            "name": "Aisyah Rahman",
            "type": "group",
            "options": "{\"channel\":\"wa\",\"source\":\"wa\"}",
            "participants": [{"email": "60123456781"}, {"email": "autosmart_admin@example.com"}],
        },
    },
    "room_log": {
        "room_id": "70004321",
        "channel_id": 101,
        "source": "wa",
        "name": "Aisyah Rahman",
        "user_id": "60123456781",
        "resolved": False,
        "is_waiting": True,
        "created_at": "2026-10-01T03:15:42Z",
    },
}

FILE_MESSAGE = {
    "id": 550001002,
    "id_str": "550001002",
    "type": "file_attachment",
    "text": "[file] https://example.com/files/brochure-request.jpg [/file]",
    "payload": {
        "url": "https://example.com/files/brochure-request.jpg",
        "caption": "Can I get the price list for this model?",
    },
    "timestamp": "2026-10-01T04:02:10Z",
}

# The example request in Appendix B, which the sample above must map to exactly.
APPENDIX_B_EXAMPLE = {
    "full_name": "Aisyah Rahman",
    "phone": "+60123456781",
    "source": "whatsapp",
    "first_message": "Hi, is the new SUV available for a test drive this weekend?",
    "external_ref": "70004321",
    "created_at": "2026-10-01T03:15:42Z",
}


def make_webhook(n: int, source: str = "wa", user_id: str | None = None, name: str = "Aisyah Rahman",
                 message: dict | None = None, timestamp: str = "2026-10-01T03:15:42Z") -> dict:
    """A copy of the Appendix A sample with its own room and message id (n keeps them unique)."""
    w = copy.deepcopy(APPENDIX_A)
    room_id, message_id = str(70100000 + n), str(550100000 + n)
    user_id = user_id if user_id is not None else "60123456781"
    w["payload"]["from"].update(name=name, email=user_id)
    w["payload"]["room"].update(id=int(room_id), id_str=room_id, name=name,
                                options=f"{{\"channel\":\"{source}\",\"source\":\"{source}\"}}")
    w["payload"]["message"] = copy.deepcopy(message) if message else w["payload"]["message"]
    w["payload"]["message"].update(id=int(message_id), id_str=message_id, timestamp=timestamp)
    w["room_log"].update(room_id=room_id, source=source, name=name, user_id=user_id, created_at=timestamp)
    return w
