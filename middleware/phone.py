"""Turn phone numbers into E.164 (for example +60123456781) for the CRM.

The rules, decided for AutoSmart (customers in Malaysia, Singapore, Indonesia):
- A number that already has a country code is used as it is, with "+" added.
  WhatsApp numbers arrive like this (60123456781), so they are the easy case.
- A local number (012-345 6783) gets the default country, Malaysia unless the
  DEFAULT_PHONE_REGION setting says otherwise.
- Only valid numbers in Malaysia, Singapore or Indonesia are accepted.
- We never guess a country. A number that is invalid for the default country
  but valid in another one (0812 0000 0009) is marked "ambiguous" so a person
  can check it, instead of being sent to the wrong market.

The answer always keeps the raw input, so nothing the customer typed is lost.
"""
import os
import re

import phonenumbers

ALLOWED_REGIONS = {"MY", "SG", "ID"}


def default_region() -> str:
    return os.getenv("DEFAULT_PHONE_REGION", "MY").upper()


def _valid_e164(text: str, region: str | None) -> str | None:
    """Parse text for a region; return the E.164 string if valid and allowed, else None."""
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    if phonenumbers.region_code_for_number(number) not in ALLOWED_REGIONS:
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_phone(raw, region: str | None = None) -> dict:
    """Return {"raw", "e164", "status"} where status is ok, missing, invalid or ambiguous.

    e164 is only filled when status is ok.
    """
    region = (region or default_region()).upper()
    text = "" if raw is None else str(raw).strip()
    digits = re.sub(r"\D", "", text)
    result = {"raw": raw, "e164": None, "status": "invalid"}

    if not digits:
        result["status"] = "missing"
        return result

    # 1. Written with "+": the country code is stated, trust it.
    if text.startswith("+"):
        e164 = _valid_e164("+" + digits, None)
        if e164:
            result.update(e164=e164, status="ok")
        return result

    # 2. No "+": try reading it as country code + number (WhatsApp style) ...
    e164 = _valid_e164("+" + digits, None)
    # 3. ... otherwise as a local number in the default country.
    if not e164:
        e164 = _valid_e164(digits, region)
    if e164:
        result.update(e164=e164, status="ok")
        return result

    # 4. Not valid for the default country. If it is valid as a local number
    #    somewhere else we serve, flag it as ambiguous instead of guessing.
    for other in sorted(ALLOWED_REGIONS - {region}):
        if _valid_e164(digits, other):
            result["status"] = "ambiguous"
            break
    return result
