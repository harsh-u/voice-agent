"""Phone number normalization helpers."""
import re


def normalize(phone: str) -> str:
    """Strip non-digits and ensure leading + for E.164."""
    digits = re.sub(r"\D", "", phone)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def to_wa_id(phone: str) -> str:
    """Return the WhatsApp phone ID format (digits only, no +)."""
    return re.sub(r"\D", "", phone)
