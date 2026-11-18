"""Paystack provider.

Collecting naira (a Nigerian Paystack business, PAYSTACK_SECRET_KEY):
  POST /transaction/initialize gives a checkout link (card, bank transfer, USSD...).
  charge.success webhook, re-checked with GET /transaction/verify/{reference}.

Paying out cedis (a *Ghanaian* Paystack business, PAYSTACK_GH_SECRET_KEY):
  Paystack only lets businesses registered in Ghana send GHS to mobile money, and
  payouts come from that business's GHS balance, which you fund yourself.
  POST /transferrecipient, then POST /transfer.
  transfer.success / transfer.failed / transfer.reversed webhooks, re-checked with
  GET /transfer/verify/{reference}.
  Paystack has no exchange-rate API, so the rate comes from NGN_PER_GHS.

Both businesses send webhooks to /webhooks/paystack; the x-paystack-signature
(HMAC-SHA512 of the body with that business's secret key) tells them apart.
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from apihttp import JsonApi, to_minor_units
from banks import BANKS_BY_CODE, GHANA_BANKS
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

# Our mobile money codes -> Paystack's Ghana codes.
MOMO_CODES = {"MTN": "MTN", "TELECEL": "VOD", "AT": "ATL"}

# Paystack transfer references must be lowercase, 16-50 characters.
PAYOUT_SUFFIX = "-payout"

PENDING_STATUSES = {"pending", "processing", "queued", "received"}
FAILED_STATUSES = {"failed", "reversed", "abandoned", "rejected"}


class PaystackProvider(JsonApi):
    name = "paystack"
    label = "Paystack"
    is_sandbox = False
    can_resolve_names = False

    def __init__(self, cfg, role, http=None):
        self.role = role
        key_setting = "PAYSTACK_SECRET_KEY" if role == "collect" else "PAYSTACK_GH_SECRET_KEY"
        required = [key_setting] + (["CUSTOMER_EMAIL"] if role == "collect" else ["NGN_PER_GHS"])
        missing = [k for k in required if not cfg.get(k)]
        if missing:
            raise ValueError(f"Paystack ({role}) needs these settings: {', '.join(missing)}")
        super().__init__(cfg["PAYSTACK_BASE_URL"], cfg[key_setting], http)
        self.customer_email = cfg.get("CUSTOMER_EMAIL")
        self.manual_rate = None
        if role == "payout":
            try:
                self.manual_rate = Decimal(str(cfg["NGN_PER_GHS"]))
            except InvalidOperation:
                raise ValueError(f"NGN_PER_GHS must be a number, got {cfg['NGN_PER_GHS']!r}") from None
            if not self.manual_rate.is_finite() or self.manual_rate <= 0:
                raise ValueError("NGN_PER_GHS must be a positive number")
        try:
            self.bank_codes = json.loads(cfg["PAYSTACK_GH_BANKS"] or "{}")
        except ValueError as exc:
            raise ValueError(f"PAYSTACK_GH_BANKS is not valid JSON: {exc}") from exc
        unknown = set(self.bank_codes) - set(BANKS_BY_CODE)
        if unknown:
            raise ValueError(f"PAYSTACK_GH_BANKS has unknown bank codes: {', '.join(sorted(unknown))}")

    def _ok(self, status_code, body):
        return body.get("status") is True

    # ---------- collecting ----------

    def create_collection(self, transfer):
        data = self._request(
            "POST",
            "/transaction/initialize",
            json={
                "email": self.customer_email,
                "amount": str(to_minor_units(transfer.total_ngn)),
                "currency": "NGN",
                "reference": transfer.reference,
                "metadata": {"phone": transfer.phone},
            },
        )["data"]
        return Collection(amount_ngn=transfer.total_ngn, pay_url=data["authorization_url"])

    # ---------- paying out ----------

    def supported_banks(self):
        return [b for b in GHANA_BANKS if b.kind == "momo" or b.code in self.bank_codes]

    def ngn_per_ghs(self):
        return self.manual_rate

    def resolve_account_name(self, bank, account_number):
        raise NotImplementedError

    def recipient_payload(self, transfer):
        bank = BANKS_BY_CODE[transfer.bank_code]
        if bank.kind == "momo":
            kind, code = "mobile_money", MOMO_CODES[bank.code]
        else:
            kind, code = "ghipss", self.bank_codes[bank.code]
        return {
            "type": kind,
            "name": transfer.account_name,
            "account_number": transfer.account_number,
            "bank_code": code,
            "currency": "GHS",
        }

    def payout(self, transfer):
        try:
            recipient = self._request("POST", "/transferrecipient", json=self.recipient_payload(transfer))["data"]
        except ProviderError as exc:
            # No money has moved yet, but a human should retry or refund.
            return PayoutResult("unknown", error=f"Could not create Paystack recipient: {exc}")
        try:
            data = self._request(
                "POST",
                "/transfer",
                json={
                    "source": "balance",
                    "amount": to_minor_units(transfer.amount_ghs),
                    "currency": "GHS",
                    "recipient": recipient["recipient_code"],
                    "reference": transfer.reference.lower() + PAYOUT_SUFFIX,
                    "reason": f"9SEND {transfer.reference}",
                },
            )["data"]
        except ProviderUnavailable as exc:
            return PayoutResult("unknown", error=str(exc))
        except ProviderError as exc:
            return PayoutResult("failed", error=str(exc))

        status = str(data.get("status", "")).lower()
        ref = data.get("transfer_code")
        if status == "success":
            return PayoutResult("success", provider_ref=ref)
        if status in PENDING_STATUSES:
            return PayoutResult("pending", provider_ref=ref)
        if status == "otp":
            return PayoutResult("unknown", provider_ref=ref,
                                error="Paystack asked for an OTP: disable OTP for transfers in the dashboard")
        if status in FAILED_STATUSES:
            return PayoutResult("failed", provider_ref=ref, error=f"Paystack transfer {status}")
        return PayoutResult("unknown", provider_ref=ref, error=f"Unexpected Paystack status {status!r}")

    # ---------- webhooks ----------

    def parse_webhook(self, headers, body: bytes):
        expected = hmac.new(self.secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(headers.get("x-paystack-signature", "").lower(), expected):
            raise WebhookAuthError()
        try:
            payload = json.loads(body)
            event = payload["event"]
            reference = str(payload["data"]["reference"])
        except (ValueError, KeyError, TypeError):
            return None

        if self.role == "collect" and event == "charge.success":
            tx = self._request("GET", f"/transaction/verify/{quote(reference, safe='')}")["data"]
            if tx.get("status") != "success" or tx.get("currency") != "NGN" or tx.get("reference") != reference:
                return None
            return PaymentReceived(reference, Decimal(str(tx["amount"])) / 100)

        if self.role == "payout" and event in {"transfer.success", "transfer.failed", "transfer.reversed"}:
            if not reference.endswith(PAYOUT_SUFFIX):
                return None
            tr = self._request("GET", f"/transfer/verify/{quote(reference, safe='')}")["data"]
            status = str(tr.get("status", "")).lower()
            ours = reference[: -len(PAYOUT_SUFFIX)].upper()
            if status == "success":
                return PayoutUpdate(ours, True, provider_ref=tr.get("transfer_code"))
            if status in FAILED_STATUSES:
                return PayoutUpdate(ours, False, provider_ref=tr.get("transfer_code"),
                                    error=f"Paystack transfer {status}")
        return None
