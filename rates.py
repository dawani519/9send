"""Quotes and money formatting. All money math uses Decimal, never float."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation

from database import utcnow

CENT = Decimal("0.01")
RATE_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class Quote:
    amount_ngn: Decimal  # the part that is converted
    fee_ngn: Decimal
    total_ngn: Decimal  # what the sender pays
    ngn_per_ghs: Decimal  # rate applied, including our margin
    amount_ghs: Decimal  # what the receiver gets
    created_at: datetime

    def to_dict(self):
        d = {k: str(getattr(self, k)) for k in ("amount_ngn", "fee_ngn", "total_ngn", "ngn_per_ghs", "amount_ghs")}
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(
            **{k: Decimal(d[k]) for k in ("amount_ngn", "fee_ngn", "total_ngn", "ngn_per_ghs", "amount_ghs")},
            created_at=datetime.fromisoformat(d["created_at"]),
        )


def make_quote(amount_ngn: Decimal, market_ngn_per_ghs: Decimal, cfg) -> Quote:
    fee = (amount_ngn * cfg["FEE_PERCENT"] / 100).quantize(CENT, ROUND_HALF_UP)
    rate = (market_ngn_per_ghs * (1 + cfg["FX_MARGIN_PERCENT"] / 100)).quantize(RATE_PLACES, ROUND_UP)
    amount_ghs = (amount_ngn / rate).quantize(CENT, ROUND_DOWN)
    return Quote(amount_ngn, fee, amount_ngn + fee, rate, amount_ghs, utcnow())


def parse_amount(text: str):
    """Parse things like '5000', '5,000', '₦5000', '50k', '5000 naira'. Returns Decimal or None."""
    cleaned = text.lower().replace(",", "").replace(" ", "").replace("₦", "")
    cleaned = re.sub(r"(ngn|naira)$", "", cleaned)
    multiplier = 1
    if cleaned.endswith("k"):
        cleaned, multiplier = cleaned[:-1], 1000
    try:
        value = Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    if value != value.quantize(CENT, ROUND_DOWN):
        return None  # more than 2 decimal places
    return value.quantize(CENT)


def ngn(value: Decimal) -> str:
    return f"₦{value:,.2f}"


def ghs(value: Decimal) -> str:
    return f"GH₵{value:,.2f}"
