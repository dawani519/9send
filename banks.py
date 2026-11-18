"""Supported receiving institutions in Ghana and account-number validation."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Bank:
    code: str  # our internal code; each provider maps it to its own
    name: str
    kind: str  # "momo" (mobile money) or "bank"


GHANA_BANKS = [
    Bank("MTN", "MTN Mobile Money", "momo"),
    Bank("TELECEL", "Telecel Cash", "momo"),
    Bank("AT", "AirtelTigo Money", "momo"),
    Bank("GCB", "GCB Bank", "bank"),
    Bank("ECOBANK", "Ecobank Ghana", "bank"),
    Bank("ACCESS", "Access Bank Ghana", "bank"),
]

BANKS_BY_CODE = {b.code: b for b in GHANA_BANKS}


def bank_menu(banks):
    return "\n".join(f"[{i}] {b.name}" for i, b in enumerate(banks, start=1))


def bank_from_choice(banks, choice: str):
    if choice.isdigit() and 1 <= int(choice) <= len(banks):
        return banks[int(choice) - 1]
    return None


def normalize_account(bank: Bank, raw: str):
    """Return the cleaned account number, or None if it isn't valid for this bank.

    Mobile money numbers are stored in local format (0241234567).
    """
    digits = re.sub(r"[\s\-]", "", raw).lstrip("+")
    if not digits.isdigit():
        return None
    if bank.kind == "momo":
        # Accept 0241234567, 233241234567 or +233241234567.
        if digits.startswith("233") and len(digits) == 12:
            digits = "0" + digits[3:]
        return digits if len(digits) == 10 and digits.startswith("0") else None
    return digits if 10 <= len(digits) <= 16 else None


def normalize_name(raw: str):
    """A receiver's name as typed by the sender, or None if it's clearly not a name."""
    name = " ".join(raw.split())
    if not (3 <= len(name) <= 100):
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", name) or len(name.split()) < 2:
        return None
    return name.upper()
