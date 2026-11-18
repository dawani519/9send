"""Flutterwave (v3 API) payment provider.

  Collect naira:  POST /charges?type=bank_transfer gives a one-off virtual account per transfer.
                  Flutterwave sends a charge.completed webhook when the sender pays.
  Pay out GHS:    POST /transfers to Ghana mobile money or bank. Asynchronous: the result
                  arrives in a transfer.completed webhook.
  Rates:          GET /transfers/rates

Webhooks are authenticated with the dashboard "secret hash" (verif-hash header) AND
re-fetched from the API before we act on them, so a forged or replayed webhook
can't trigger a payout.
"""
import hmac
import json
import logging
import time
from decimal import Decimal

from apihttp import JsonApi
from banks import GHANA_BANKS, BANKS_BY_CODE
from payments import (
    Collection,
    PaymentReceived,
    PayoutResult,
    PayoutUpdate,
    ProviderError,
    ProviderUnavailable,
    WebhookAuthError,
)

log = logging.getLogger(__name__)

# Our mobile money codes -> Flutterwave's Ghana operator codes.
# Telecel Cash was Vodafone Cash; AirtelTigo Money uses the TIGO code.
MOMO_OPERATORS = {"MTN": "MTN", "TELECEL": "VODAFONE", "AT": "TIGO"}

PAYOUT_SUFFIX = "-PAYOUT"
RATE_CACHE_SECONDS = 300


class FlutterwaveProvider(JsonApi):
    name = "flutterwave"
    label = "Flutterwave"
    is_sandbox = False
    can_resolve_names = False  # Flutterwave's account lookup doesn't cover Ghana

    def __init__(self, cfg, role="both", http=None):
        self.role = role
        required = ["FLUTTERWAVE_SECRET_KEY", "FLUTTERWAVE_WEBHOOK_HASH"]
        if role in ("collect", "both"):
            required.append("CUSTOMER_EMAIL")
        missing = [k for k in required if not cfg.get(k)]
        if missing:
            raise ValueError(f"Flutterwave needs these settings: {', '.join(missing)}")
        super().__init__(cfg["FLUTTERWAVE_BASE_URL"], cfg["FLUTTERWAVE_SECRET_KEY"], http)
        self.webhook_hash = cfg["FLUTTERWAVE_WEBHOOK_HASH"]
        self.customer_email = cfg.get("CUSTOMER_EMAIL")
        self.debit_currency = cfg["FLUTTERWAVE_DEBIT_CURRENCY"]
        try:
            self.bank_routing = json.loads(cfg["FLUTTERWAVE_GH_BANKS"] or "{}")
        except ValueError as exc:
            raise ValueError(f"FLUTTERWAVE_GH_BANKS is not valid JSON: {exc}") from exc
        unknown = set(self.bank_routing) - set(BANKS_BY_CODE)
        if unknown:
            raise ValueError(f"FLUTTERWAVE_GH_BANKS has unknown bank codes: {', '.join(sorted(unknown))}")
        self._rate = None  # (value, fetched_at)

    def _ok(self, status_code, body):
        return body.get("status") == "success"

    # ---------- interface ----------

    def supported_banks(self):
        return [b for b in GHANA_BANKS if b.kind == "momo" or b.code in self.bank_routing]

    def ngn_per_ghs(self):
        if self._rate and time.monotonic() - self._rate[1] < RATE_CACHE_SECONDS:
            return self._rate[0]
        data = self._request(
            "GET",
            "/transfers/rates",
            params={"amount": "1000", "destination_currency": "GHS", "source_currency": "NGN"},
        )["data"]
        # Derive the rate from the amounts so we don't depend on which way "rate" is quoted.
        source = Decimal(str(data["source"]["amount"]))
        destination = Decimal(str(data["destination"]["amount"]))
        if source <= 0 or destination <= 0:
            raise ProviderError(f"Flutterwave returned an unusable rate: {data}")
        rate = source / destination
        self._rate = (rate, time.monotonic())
        return rate

    def resolve_account_name(self, bank, account_number):
        raise NotImplementedError("Flutterwave can't resolve Ghana account names")

    def create_collection(self, transfer):
        body = self._request(
            "POST",
            "/charges",
            params={"type": "bank_transfer"},
            json={
                "tx_ref": transfer.reference,
                "amount": str(transfer.total_ngn),
                "currency": "NGN",
                "email": self.customer_email,
                "phone_number": transfer.phone,
                "narration": f"9SEND {transfer.reference}",
                "is_permanent": False,
            },
        )
        auth = (body.get("meta") or {}).get("authorization") or {}
        if not auth.get("transfer_account"):
            raise ProviderError(f"Flutterwave didn't return a payment account: {body.get('message')}")
        return Collection(
            bank_name=auth.get("transfer_bank", "Bank"),
            account_number=str(auth["transfer_account"]),
            account_name=auth.get("account_name"),
            amount_ngn=Decimal(str(auth.get("transfer_amount") or transfer.total_ngn)),
            expires=str(auth["account_expiration"]) if auth.get("account_expiration") else None,
        )

    def payout_payload(self, transfer):
        bank = BANKS_BY_CODE[transfer.bank_code]
        payload = {
            "amount": str(transfer.amount_ghs),
            "currency": "GHS",
            "debit_currency": self.debit_currency,
            "beneficiary_name": transfer.account_name,
            "narration": f"9SEND {transfer.reference}",
            "reference": transfer.reference + PAYOUT_SUFFIX,
        }
        if bank.kind == "momo":
            payload["account_bank"] = MOMO_OPERATORS[bank.code]
            payload["account_number"] = "233" + transfer.account_number[1:]
        else:
            routing = self.bank_routing[bank.code]
            payload["account_bank"] = routing["code"]
            payload["account_number"] = transfer.account_number
            payload["destination_branch_code"] = routing["branch"]
        return payload

    def payout(self, transfer):
        try:
            data = self._request("POST", "/transfers", json=self.payout_payload(transfer))["data"]
        except ProviderUnavailable as exc:
            # The transfer may or may not exist at Flutterwave. Never retry or refund blindly.
            return PayoutResult("unknown", error=str(exc))
        except ProviderError as exc:
            return PayoutResult("failed", error=str(exc))
        return PayoutResult("pending", provider_ref=str(data["id"]))

    def parse_webhook(self, headers, body: bytes):
        received = headers.get("verif-hash", "")
        if not received or not hmac.compare_digest(received, self.webhook_hash):
            raise WebhookAuthError()
        try:
            payload = json.loads(body)
            event = payload.get("event")
            event_id = int(payload["data"]["id"])
        except (ValueError, KeyError, TypeError, AttributeError):
            log.info("Ignoring unparseable Flutterwave webhook")
            return None

        if event == "charge.completed" and self.role in ("collect", "both"):
            tx = self._request("GET", f"/transactions/{event_id}/verify")["data"]
            if tx.get("status") != "successful" or tx.get("currency") != "NGN":
                log.info("Charge %s not successful in NGN (%s %s)", event_id, tx.get("status"), tx.get("currency"))
                return None
            return PaymentReceived(str(tx["tx_ref"]), Decimal(str(tx["amount"])))

        if event == "transfer.completed" and self.role in ("payout", "both"):
            tr = self._request("GET", f"/transfers/{event_id}")["data"]
            reference = str(tr.get("reference", ""))
            if not reference.endswith(PAYOUT_SUFFIX):
                return None
            status = str(tr.get("status", "")).upper()
            if status not in {"SUCCESSFUL", "FAILED"}:
                return None
            return PayoutUpdate(
                reference=reference[: -len(PAYOUT_SUFFIX)],
                success=status == "SUCCESSFUL",
                provider_ref=str(tr["id"]),
                error=tr.get("complete_message"),
            )

        return None
