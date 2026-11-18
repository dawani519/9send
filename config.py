"""Application settings, read from environment variables (a local .env file is supported).

See .env.example for every setting and what it does.
"""
import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_url():
    url = os.getenv("DATABASE_URL", "sqlite:///9send.db")
    # Render/Heroku hand out postgres:// URLs, which SQLAlchemy 2 no longer accepts.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    DATABASE_URL = _database_url()

    # Public https URL of this service (e.g. https://9send.onrender.com).
    # Used to validate Twilio signatures behind Render's proxy.
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    # Twilio
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    # On by default whenever an auth token is configured.
    VALIDATE_TWILIO_SIGNATURE = _bool("VALIDATE_TWILIO_SIGNATURE", default=bool(TWILIO_AUTH_TOKEN))

    # Payments. Collecting naira and paying out cedis can use different providers:
    #   COLLECTION_PROVIDER: sandbox, flutterwave, paystack, squad
    #   PAYOUT_PROVIDER:     sandbox, flutterwave, paystack (needs a Ghana Paystack business)
    # Either one left empty falls back to PAYMENT_PROVIDER.
    PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "sandbox")
    COLLECTION_PROVIDER = os.getenv("COLLECTION_PROVIDER", "")
    PAYOUT_PROVIDER = os.getenv("PAYOUT_PROVIDER", "")

    # Paystack, Squad and Flutterwave require an email on every payment; the bot doesn't collect one.
    CUSTOMER_EMAIL = os.getenv("CUSTOMER_EMAIL", "")

    # Manual exchange rate (naira per cedi), used by the sandbox and by Paystack payouts.
    # Flutterwave payouts use Flutterwave's live rate instead.
    NGN_PER_GHS = os.getenv("NGN_PER_GHS", "")

    # Pricing
    FEE_PERCENT = Decimal(os.getenv("FEE_PERCENT", "1"))
    # Added on top of the provider's exchange rate (your FX margin).
    FX_MARGIN_PERCENT = Decimal(os.getenv("FX_MARGIN_PERCENT", "0"))
    MIN_AMOUNT_NGN = Decimal(os.getenv("MIN_AMOUNT_NGN", "1000"))
    MAX_AMOUNT_NGN = Decimal(os.getenv("MAX_AMOUNT_NGN", "1000000"))
    # A quote older than this is refreshed before the transfer is created.
    QUOTE_TTL_MINUTES = int(os.getenv("QUOTE_TTL_MINUTES", "15"))

    # A conversation idle longer than this starts again from the menu.
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))

    # --- Sandbox provider ---
    # Secret for signing test POSTs to /webhooks/sandbox (header X-9send-Signature).
    PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")
    # Account shown to sandbox users as where to "pay".
    POOL_BANK = os.getenv("POOL_BANK", "Zenith Bank")
    POOL_ACCOUNT_NUMBER = os.getenv("POOL_ACCOUNT_NUMBER", "2214567890")
    POOL_ACCOUNT_NAME = os.getenv("POOL_ACCOUNT_NAME", "9SEND POOL")

    # --- Flutterwave provider ---
    FLUTTERWAVE_BASE_URL = os.getenv("FLUTTERWAVE_BASE_URL", "https://api.flutterwave.com/v3").rstrip("/")
    FLUTTERWAVE_SECRET_KEY = os.getenv("FLUTTERWAVE_SECRET_KEY", "")
    # The "Secret hash" set in the Flutterwave dashboard under Settings > Webhooks.
    FLUTTERWAVE_WEBHOOK_HASH = os.getenv("FLUTTERWAVE_WEBHOOK_HASH", "")
    # Balance that funds GHS payouts ("NGN" lets Flutterwave convert from your naira balance).
    FLUTTERWAVE_DEBIT_CURRENCY = os.getenv("FLUTTERWAVE_DEBIT_CURRENCY", "NGN")
    # JSON mapping our bank codes (banks.py) to Flutterwave bank and branch codes, e.g.
    # {"GCB": {"code": "GH040100", "branch": "GH040101"}}. Run scripts/flutterwave_ghana_banks.py
    # to look them up. Banks not listed here are hidden; mobile money always works.
    FLUTTERWAVE_GH_BANKS = os.getenv("FLUTTERWAVE_GH_BANKS", "{}")

    # --- Paystack ---
    PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co").rstrip("/")
    # Nigerian Paystack business: collects naira.
    PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
    # Ghanaian Paystack business: pays out cedis from its GHS balance.
    PAYSTACK_GH_SECRET_KEY = os.getenv("PAYSTACK_GH_SECRET_KEY", "")
    # JSON mapping our bank codes to Paystack Ghana bank codes, e.g. {"GCB": "<code>"}.
    # Run scripts/paystack_ghana_banks.py to look them up. Mobile money always works.
    PAYSTACK_GH_BANKS = os.getenv("PAYSTACK_GH_BANKS", "{}")

    # --- Squad (collecting naira only) ---
    # Sandbox by default; set https://api-d.squadco.com for live.
    SQUAD_BASE_URL = os.getenv("SQUAD_BASE_URL", "https://sandbox-api-d.squadco.com").rstrip("/")
    SQUAD_SECRET_KEY = os.getenv("SQUAD_SECRET_KEY", "")
