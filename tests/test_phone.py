"""Every number from Appendix C, plus empty and garbage input.

Run from the project folder:  python -m pytest -v
"""
import pytest

from middleware.phone import normalize_phone

APPENDIX_C = [
    ("60123456781", "+60123456781"),        # WhatsApp style, country code, no +
    ("+60 12-345 6782", "+60123456782"),    # spaces and dashes
    ("012-345 6783", "+60123456783"),       # Malaysian local, default country used
    ("011-2345 6784", "+601123456784"),     # Malaysian 011 local
    ("6581234565", "+6581234565"),          # Singapore, country code, no +
    ("+65 9123 4566", "+6591234566"),       # Singapore with +
    ("6281200000007", "+6281200000007"),    # Indonesia, country code, no +
    ("+62 812-0000-0008", "+6281200000008"),  # Indonesia with +
]


@pytest.mark.parametrize("raw, expected", APPENDIX_C)
def test_appendix_c_numbers_become_e164(raw, expected):
    result = normalize_phone(raw)
    assert result["status"] == "ok"
    assert result["e164"] == expected


def test_indonesian_local_number_is_ambiguous_not_guessed():
    # Valid as an Indonesian local number, invalid as Malaysian: flag it, do not guess.
    result = normalize_phone("0812 0000 0009")
    assert result["status"] == "ambiguous"
    assert result["e164"] is None
    assert result["raw"] == "0812 0000 0009"   # the original is kept


def test_same_number_is_fine_when_default_country_is_indonesia():
    # The channel upgrade: if the chat came in on the Indonesian line, it is valid.
    assert normalize_phone("0812 0000 0009", region="ID")["e164"] == "+6281200000009"


def test_too_short_is_invalid():
    result = normalize_phone("12345")
    assert result["status"] == "invalid"
    assert result["e164"] is None


@pytest.mark.parametrize("raw", [None, "", "   ", "abc"])
def test_empty_or_no_digits_is_missing(raw):
    assert normalize_phone(raw)["status"] == "missing"


def test_number_outside_our_three_countries_is_not_accepted():
    # A valid UK number: AutoSmart only serves MY, SG, ID, so a person checks it.
    assert normalize_phone("+44 20 7946 0018")["status"] == "invalid"


def test_default_country_comes_from_setting(monkeypatch):
    monkeypatch.setenv("DEFAULT_PHONE_REGION", "SG")
    assert normalize_phone("9123 4566")["e164"] == "+6591234566"
