"""Payment providers.

Money moves in two legs, and each can use a different provider:

  COLLECTION_PROVIDER  collects the sender's naira:  sandbox, flutterwave, paystack, squad
  PAYOUT_PROVIDER      pays the receiver in cedis:   sandbox, flutterwave, paystack (Ghana business)

A collection provider implements:
  name, is_sandbox
  create_collection(transfer) -> Collection      where/how the sender pays
  parse_webhook(headers, body) -> event|None

A payout provider implements:
  name, is_sandbox, can_resolve_names
  supported_banks() -> list[Bank]
  ngn_per_ghs() -> Decimal                       market rate, before our margin
  resolve_account_name(bank, number) -> str|None (only if can_resolve_names)
  payout(transfer) -> PayoutResult
  parse_webhook(headers, body) -> event|None

parse_webhook authenticates the request (raising WebhookAuthError if it isn't
authentic) and re-checks the event with the provider's API before returning it.

Providers raise ProviderError when the provider rejects a request, and
ProviderUnavailable when the outcome is unknown (network error, timeout, 5xx).
"""
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from flask import current_app

from banks import GHANA_BANKS

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """The provider rejected the request."""


class ProviderUnavailable(ProviderError):
    """We don't know whether the request went through."""


class WebhookAuthError(Exception):
    pass


@dataclass
class Collection:
    """Either bank account details to transfer to, or a checkout link (pay_url)."""

    amount_ngn: Decimal
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    expires: Optional[str] = None
    pay_url: Optional[str] = None


@dataclass
class PayoutResult:
    status: str  # "success", "pending" (result comes by webhook), "failed" or "unknown"
    provider_ref: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PaymentReceived:
    reference: str
    amount_ngn: Decimal


@dataclass
class PayoutUpdate:
    reference: str
    success: bool
    provider_ref: Optional[str] = None
    error: Optional[str] = None


SANDBOX_SIGNATURE_HEADER = "X-9send-Signature"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class SandboxProvider:
    """Moves no real money. Every account resolves and every payout succeeds."""

    name = "sandbox"
    is_sandbox = True
    can_resolve_names = True

    def __init__(self, cfg, role=None):
        self.cfg = cfg
        self.webhook_secret = cfg["PAYMENT_WEBHOOK_SECRET"]

    def supported_banks(self):
        return list(GHANA_BANKS)

    def ngn_per_ghs(self):
        return Decimal(self.cfg.get("NGN_PER_GHS") or "132")

    def resolve_account_name(self, bank, account_number):
        return f"TEST RECEIVER {account_number[-4:]}"

    def create_collection(self, transfer):
        return Collection(
            amount_ngn=transfer.total_ngn,
            bank_name=self.cfg["POOL_BANK"],
            account_number=self.cfg["POOL_ACCOUNT_NUMBER"],
            account_name=self.cfg["POOL_ACCOUNT_NAME"],
        )

    def payout(self, transfer):
        log.info("SANDBOX payout %s: GHS %s to %s", transfer.reference, transfer.amount_ghs, transfer.account_number)
        return PayoutResult("success", provider_ref=f"SBX-{transfer.reference}")

    def parse_webhook(self, headers, body: bytes):
        """Body: {"event": "payment.received", "reference": "9S...", "amount": "5050.00"},
        signed with HMAC-SHA256(PAYMENT_WEBHOOK_SECRET) in the X-9send-Signature header."""
        if not self.webhook_secret:
            log.warning("Rejecting webhook: PAYMENT_WEBHOOK_SECRET is not set")
            raise WebhookAuthError()
        if not hmac.compare_digest(sign(self.webhook_secret, body), headers.get(SANDBOX_SIGNATURE_HEADER, "")):
            raise WebhookAuthError()
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("event") != "payment.received":
            return None
        try:
            return PaymentReceived(str(payload["reference"]), Decimal(str(payload["amount"])))
        except (KeyError, InvalidOperation):
            return None


def _registry():
    from flutterwave import FlutterwaveProvider
    from paystack import PaystackProvider
    from squad import SquadProvider

    collectors = {
        "sandbox": SandboxProvider,
        "flutterwave": FlutterwaveProvider,
        "paystack": PaystackProvider,
        "squad": SquadProvider,
    }
    payouts = {
        "sandbox": SandboxProvider,
        "flutterwave": FlutterwaveProvider,
        "paystack": PaystackProvider,
    }
    return collectors, payouts


def build_providers(cfg):
    """Returns (collector, payout)."""
    collectors, payouts = _registry()
    collect_name = cfg.get("COLLECTION_PROVIDER") or cfg["PAYMENT_PROVIDER"]
    payout_name = cfg.get("PAYOUT_PROVIDER") or cfg["PAYMENT_PROVIDER"]

    if collect_name not in collectors:
        raise ValueError(f"Unknown COLLECTION_PROVIDER {collect_name!r}; available: {', '.join(collectors)}")
    if payout_name not in payouts:
        hint = " (Squad can't pay out to Ghana)" if payout_name == "squad" else ""
        raise ValueError(f"Unknown PAYOUT_PROVIDER {payout_name!r}{hint}; available: {', '.join(payouts)}")
    if (collect_name == "sandbox") != (payout_name == "sandbox"):
        raise ValueError(
            "Can't mix the sandbox with a real provider: you would collect real money and fake the payout, "
            "or pay out real money for a fake payment. Use sandbox for both or neither."
        )

    if collect_name == payout_name and collect_name in ("sandbox", "flutterwave"):
        shared = collectors[collect_name](cfg, role="both")
        return shared, shared
    return collectors[collect_name](cfg, role="collect"), payouts[payout_name](cfg, role="payout")


def get_collector():
    return current_app.extensions["collector"]


def get_payout():
    return current_app.extensions["payout"]


def providers_named(name):
    """The active provider instances with this name (one, or two for Paystack)."""
    found = []
    for p in (get_collector(), get_payout()):
        if p.name == name and p not in found:
            found.append(p)
    return found
