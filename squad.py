"""Squad (GTCO) provider: collecting naira only.

Squad's transfer API pays Nigerian bank accounts in NGN only, so it can't pay
out to Ghana. Pair it with another payout provider.

  POST /transaction/initiate gives a checkout link.
  charge_successful webhook (x-squad-encrypted-body: uppercase hex HMAC-SHA512 of
  the body with the secret key), re-checked with GET /transaction/verify/{ref}.
"""
import hashlib
import hmac
import json
from decimal import Decimal
from urllib.parse import quote

from apihttp import JsonApi, to_minor_units
from payments import Collection, PaymentReceived, WebhookAuthError


class SquadProvider(JsonApi):
    name = "squad"
    label = "Squad"
    is_sandbox = False

    def __init__(self, cfg, role="collect", http=None):
        missing = [k for k in ("SQUAD_SECRET_KEY", "CUSTOMER_EMAIL") if not cfg.get(k)]
        if missing:
            raise ValueError(f"Squad needs these settings: {', '.join(missing)}")
        super().__init__(cfg["SQUAD_BASE_URL"], cfg["SQUAD_SECRET_KEY"], http)
        self.customer_email = cfg["CUSTOMER_EMAIL"]

    def _ok(self, status_code, body):
        return body.get("status") == 200 or body.get("success") is True

    def create_collection(self, transfer):
        data = self._request(
            "POST",
            "/transaction/initiate",
            json={
                "amount": to_minor_units(transfer.total_ngn),
                "email": self.customer_email,
                "currency": "NGN",
                "initiate_type": "inline",
                "transaction_ref": transfer.reference,
            },
        )["data"]
        return Collection(amount_ngn=transfer.total_ngn, pay_url=data["checkout_url"])

    def parse_webhook(self, headers, body: bytes):
        expected = hmac.new(self.secret_key.encode(), body, hashlib.sha512).hexdigest().upper()
        if not hmac.compare_digest(headers.get("x-squad-encrypted-body", "").upper(), expected):
            raise WebhookAuthError()
        try:
            payload = json.loads(body)
            event = payload["Event"]
            reference = str(payload["TransactionRef"])
        except (ValueError, KeyError, TypeError):
            return None
        if event != "charge_successful":
            return None
        tx = self._request("GET", f"/transaction/verify/{quote(reference, safe='')}")["data"]
        if (
            str(tx.get("transaction_status", "")).lower() != "success"
            or tx.get("transaction_currency_id") != "NGN"
            or tx.get("transaction_ref") != reference
        ):
            return None
        # Squad amounts are in kobo. If that were ever wrong, the payment would look
        # 100x too small and be held as underpaid, never overpaid.
        return PaymentReceived(reference, Decimal(str(tx["transaction_amount"])) / 100)
