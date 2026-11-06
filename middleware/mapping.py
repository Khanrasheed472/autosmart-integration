"""Turn a Qiscus New Session webhook (Appendix A) into a CRM lead (Appendix B).

R1 lives here: field mapping, channel source, non-text first messages.
"""
import json
import re

from .phone import normalize_phone

SOURCE_MAP = {"wa": "whatsapp", "fb": "facebook", "ig": "instagram"}
FILE_TAG = re.compile(r"\[file\].*?\[/file\]", re.S)


class WebhookError(ValueError):
    """The body is not a New Session webhook we can use."""


def _get(data, *path):
    """Walk nested dictionaries safely: _get(w, "payload", "from", "name")."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def dedupe_key(webhook) -> str:
    """The stamp for one webhook: room id + message id.

    A redelivery has an identical body (Appendix A), so it has the same stamp.
    """
    if not isinstance(webhook, dict):
        raise WebhookError("body must be a JSON object")
    room_id = _get(webhook, "payload", "room", "id_str") or _get(webhook, "payload", "room", "id") \
        or _get(webhook, "room_log", "room_id")
    message_id = _get(webhook, "payload", "message", "id_str") or _get(webhook, "payload", "message", "id")
    if not room_id or not message_id:
        raise WebhookError("payload.room.id and payload.message.id are required")
    return f"{room_id}:{message_id}"


def channel_code(webhook) -> str:
    """wa, fb, ig ... from room_log.source, or from the JSON text in payload.room.options."""
    code = _get(webhook, "room_log", "source")
    if not code:
        options = _get(webhook, "payload", "room", "options")
        try:
            code = json.loads(options).get("source") if isinstance(options, str) else None
        except (ValueError, AttributeError):
            code = None
    return str(code or "").strip().lower()


def customer_id(webhook) -> str:
    """Who the customer is on that channel: a phone for WhatsApp, a platform id otherwise."""
    value = _get(webhook, "room_log", "user_id") or _get(webhook, "payload", "from", "email") \
        or _get(webhook, "payload", "from", "id_str") or ""
    return str(value).strip()


def customer_key(webhook) -> str:
    return f"{channel_code(webhook) or 'other'}:{customer_id(webhook)}"


def first_message(message) -> str:
    """Text as is; for a photo or file use its caption, never the [file] url [/file] placeholder."""
    if not isinstance(message, dict):
        return ""
    kind = message.get("type") or "text"
    text = str(message.get("text") or "")
    extra = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    if kind == "text":
        result = text
    else:
        caption = extra.get("caption") or FILE_TAG.sub("", text).strip()
        result = caption or f"[{kind.replace('_', ' ')}]"
    return result.strip()[:1000]


def map_to_lead(webhook) -> dict:
    """Return {"lead", "phone", "customer_key", "problem"}.

    problem is None when the lead can go to the CRM, otherwise the reason it is parked.
    """
    source = SOURCE_MAP.get(channel_code(webhook), "other")
    payload = _get(webhook, "payload") or {}

    name = str(_get(payload, "from", "name") or _get(webhook, "room_log", "name") or "").strip()
    lead = {
        "full_name": (name or "Unknown customer")[:100],
        "source": source,
        "first_message": first_message(_get(payload, "message")),
        "external_ref": str(_get(webhook, "room_log", "room_id") or _get(payload, "room", "id_str")
                            or _get(payload, "room", "id") or ""),
        "created_at": _get(payload, "message", "timestamp") or _get(webhook, "room_log", "created_at"),
    }

    phone = None
    problem = None
    if source == "whatsapp":
        # WhatsApp: the field is the customer's phone, and the CRM requires it.
        phone = normalize_phone(customer_id(webhook))
        if phone["status"] == "ok":
            lead["phone"] = phone["e164"]
        else:
            problem = f"WhatsApp phone is {phone['status']}: {phone['raw']!r}"
    elif source == "other":
        # Unknown channel: send a phone only if the id really is one.
        candidate = normalize_phone(customer_id(webhook))
        if candidate["status"] == "ok":
            phone = candidate
            lead["phone"] = candidate["e164"]
    # Facebook and Instagram: the field is a platform id, never a phone, so none is sent.

    lead = {k: v for k, v in lead.items() if v not in (None, "")}
    lead.setdefault("full_name", "Unknown customer")
    return {"lead": lead, "phone": phone, "customer_key": customer_key(webhook), "problem": problem}
