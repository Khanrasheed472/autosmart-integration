"""R1: the webhook fields land in the right CRM fields."""
import copy

import pytest

from middleware.mapping import WebhookError, dedupe_key, map_to_lead
from tests.samples import APPENDIX_A, APPENDIX_B_EXAMPLE, FILE_MESSAGE, make_webhook


def test_appendix_a_sample_maps_exactly_to_appendix_b_example():
    result = map_to_lead(APPENDIX_A)
    assert result["problem"] is None
    assert result["lead"] == APPENDIX_B_EXAMPLE


def test_file_attachment_uses_the_caption_not_the_file_placeholder():
    w = make_webhook(1, message=FILE_MESSAGE)
    assert map_to_lead(w)["lead"]["first_message"] == "Can I get the price list for this model?"


def test_file_without_caption_gets_a_readable_placeholder():
    message = copy.deepcopy(FILE_MESSAGE)
    message["payload"].pop("caption")
    assert map_to_lead(make_webhook(2, message=message))["lead"]["first_message"] == "[file attachment]"


@pytest.mark.parametrize("code, expected", [("wa", "whatsapp"), ("fb", "facebook"),
                                            ("ig", "instagram"), ("line", "other")])
def test_channel_source_mapping(code, expected):
    w = make_webhook(3, source=code, user_id="60123456781" if code == "wa" else "psid-123")
    assert map_to_lead(w)["lead"]["source"] == expected


def test_facebook_platform_id_is_never_sent_as_a_phone():
    lead = map_to_lead(make_webhook(4, source="fb", user_id="2345678901234567"))["lead"]
    assert "phone" not in lead


def test_source_falls_back_to_the_options_text_when_room_log_has_none():
    w = make_webhook(5, source="ig", user_id="ig-777")
    w["room_log"].pop("source")
    assert map_to_lead(w)["lead"]["source"] == "instagram"


def test_whatsapp_local_number_is_normalised():
    assert map_to_lead(make_webhook(6, user_id="012-345 6783"))["lead"]["phone"] == "+60123456783"


@pytest.mark.parametrize("bad", ["12345", "0812 0000 0009", ""])
def test_whatsapp_phone_that_cannot_be_fixed_is_parked(bad):
    assert map_to_lead(make_webhook(7, user_id=bad))["problem"] is not None


def test_long_message_and_long_name_are_cut_to_crm_limits():
    message = {"id": 1, "id_str": "1", "type": "text", "text": "x" * 1500, "payload": {}}
    lead = map_to_lead(make_webhook(8, name="N" * 150, message=message))["lead"]
    assert len(lead["first_message"]) == 1000 and len(lead["full_name"]) == 100


def test_dedupe_key_is_room_plus_message_and_needs_both():
    assert dedupe_key(APPENDIX_A) == "70004321:550001001"
    with pytest.raises(WebhookError):
        dedupe_key({"payload": {}})
