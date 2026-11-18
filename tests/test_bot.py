import json
import warnings
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from app import create_app
from database import Transfer, TransferStatus, UserSession, utcnow
from payments import SANDBOX_SIGNATURE_HEADER as SIGNATURE_HEADER, PayoutResult, sign
from rates import make_quote, parse_amount

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

PHONE = "+2348012345678"
SECRET = "test-secret"


@pytest.fixture
def app(tmp_path):
    return create_app({
        "DATABASE_URL": f"sqlite:///{tmp_path}/test.db",
        "VALIDATE_TWILIO_SIGNATURE": False,
        "TWILIO_ACCOUNT_SID": None,
        "TWILIO_AUTH_TOKEN": None,
        "PAYMENT_PROVIDER": "sandbox",
        "PAYMENT_WEBHOOK_SECRET": SECRET,
        "NGN_PER_GHS": Decimal("132"),
        "FEE_PERCENT": Decimal("1"),
        "FX_MARGIN_PERCENT": Decimal("0"),
        "MIN_AMOUNT_NGN": Decimal("1000"),
        "MAX_AMOUNT_NGN": Decimal("1000000"),
    })


@pytest.fixture
def send(app):
    client = app.test_client()

    def _send(body, phone=PHONE):
        resp = client.post("/whatsapp", data={"From": f"whatsapp:{phone}", "Body": body})
        assert resp.status_code == 200, resp.data
        return resp.get_data(as_text=True)

    return _send


def get_transfers(app):
    with app.app_context():
        with app.extensions["db"].Session() as s:
            return s.query(Transfer).all()


def start_transfer(send, amount="50000"):
    send("hi")
    send("1")
    send(amount)
    send("yes")
    send("1")  # MTN MoMo
    send("0241234567")
    return send("yes")


# ---------- pure helpers ----------

@pytest.mark.parametrize("text,expected", [
    ("5000", Decimal("5000")),
    ("5,000", Decimal("5000")),
    ("₦5000", Decimal("5000")),
    ("50k", Decimal("50000")),
    ("5000 naira", Decimal("5000")),
    ("1234.50", Decimal("1234.50")),
    ("abc", None),
    ("-5", None),
    ("0", None),
    ("1.234", None),
    ("nan", None),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_quote_is_consistent(app):
    q = make_quote(Decimal("50000"), Decimal("132"), app.config)
    assert q.fee_ngn == Decimal("500.00")
    assert q.total_ngn == Decimal("50500.00")
    assert q.amount_ghs == Decimal("378.78")  # 50000 / 132, rounded down


def test_fx_margin_reduces_payout(app):
    app.config["FX_MARGIN_PERCENT"] = Decimal("2")
    q = make_quote(Decimal("50000"), Decimal("132"), app.config)
    assert q.ngn_per_ghs == Decimal("134.64")
    assert q.amount_ghs == Decimal("371.36")


# ---------- conversation ----------

def test_first_message_shows_menu(send):
    reply = send("hello there")
    assert "Send money" in reply and "Test mode" in reply


def test_full_sandbox_flow(app, send):
    reply = start_transfer(send)
    assert "₦50,500.00" in reply and "9S" in reply

    reply = send("paid")
    assert "complete" in reply and "GH₵378.78" in reply

    [t] = get_transfers(app)
    assert t.status == TransferStatus.COMPLETED
    assert t.provider_ref == f"SBX-{t.reference}"
    assert "Send money" in send("anything")  # back at the menu


def test_invalid_inputs_reprompt_instead_of_resetting(send):
    send("hi")
    send("1")
    assert "number" in send("lots")
    assert "minimum" in send("50").lower()
    assert "maximum" in send("5000000").lower()
    send("5000")
    assert "YES" in send("maybe")
    send("yes")
    assert "choose" in send("99").lower()
    send("2")  # Telecel
    assert "doesn't look right" in send("12345")
    reply = send("+233 24 123 4567")
    assert "0241234567" in reply


def test_menu_word_resets_mid_flow(send):
    send("hi")
    send("1")
    assert "Send money" in send("menu")


def test_cancel_while_awaiting_payment(app, send):
    start_transfer(send)
    reply = send("cancel")
    assert "cancelled" in reply
    [t] = get_transfers(app)
    assert t.status == TransferStatus.CANCELLED


def test_no_on_recipient_reenters_account(send):
    send("hi"); send("1"); send("5000"); send("yes"); send("4")  # GCB
    send("1234567890")
    assert "account number" in send("no")
    assert "1111222233" in send("1111222233")


def test_rates_and_status_menu(send):
    send("hi")
    assert "Today's rate" in send("2")
    assert "haven't made any transfers" in send("3")


def test_stale_session_restarts(app, send):
    send("hi")
    send("1")
    with app.app_context():
        with app.extensions["db"].Session.begin() as s:
            s.execute(update(UserSession).values(updated_at=utcnow() - timedelta(hours=2)))
    assert "Send money" in send("5000")


def test_users_are_isolated(app, send):
    start_transfer(send)
    assert "Send money" in send("hi", phone="+2348099999999")
    assert "₦50,500.00" in send("xyz")  # first user still awaiting payment


# ---------- payment webhook ----------

def post_webhook(app, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return app.test_client().post(
        "/webhooks/sandbox",
        data=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, body)},
    )


def test_webhook_rejects_bad_signature(app, send):
    start_transfer(send)
    [t] = get_transfers(app)
    resp = post_webhook(app, {"event": "payment.received", "reference": t.reference, "amount": "50500"}, secret="wrong")
    assert resp.status_code == 401
    assert get_transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT


def test_webhook_rejects_everything_without_secret(app, send):
    app.extensions["payout"].webhook_secret = ""
    resp = post_webhook(app, {"event": "payment.received", "reference": "X", "amount": "1"}, secret="")
    assert resp.status_code == 401


def test_webhook_completes_and_is_idempotent(app, send):
    start_transfer(send)
    [t] = get_transfers(app)
    payload = {"event": "payment.received", "reference": t.reference, "amount": "50500.00"}
    assert post_webhook(app, payload).status_code == 200
    assert post_webhook(app, payload).status_code == 200  # retry: must not pay out twice
    [t] = get_transfers(app)
    assert t.status == TransferStatus.COMPLETED
    # The user's next message reports the result.
    assert "complete" in send("hello?")


def test_webhook_underpayment_goes_to_review(app, send):
    start_transfer(send)
    [t] = get_transfers(app)
    post_webhook(app, {"event": "payment.received", "reference": t.reference, "amount": "100"})
    [t] = get_transfers(app)
    assert t.status == TransferStatus.NEEDS_REVIEW


def test_rejected_payout_is_failed(app, send):
    app.extensions["payout"].payout = lambda t: PayoutResult("failed", error="invalid account")
    start_transfer(send)
    assert "couldn't complete" in send("paid")
    [t] = get_transfers(app)
    assert t.status == TransferStatus.FAILED and "invalid account" in t.failure_reason


def test_crashed_payout_needs_review_not_refund(app, send):
    # We can't know whether money moved, so a human must check before any refund.
    def boom(transfer):
        raise RuntimeError("provider down")

    app.extensions["payout"].payout = boom
    start_transfer(send)
    assert "need to check" in send("paid")
    [t] = get_transfers(app)
    assert t.status == TransferStatus.NEEDS_REVIEW and "provider down" in t.failure_reason


def test_payment_after_cancel_needs_review(app, send):
    start_transfer(send)
    send("cancel")
    [t] = get_transfers(app)
    post_webhook(app, {"event": "payment.received", "reference": t.reference, "amount": "50500"})
    assert get_transfers(app)[0].status == TransferStatus.NEEDS_REVIEW


def test_stale_quote_is_refreshed(app, send):
    send("hi"); send("1"); send("50000"); send("yes"); send("1"); send("0241234567")
    app.config["QUOTE_TTL_MINUTES"] = -1  # every quote is now stale
    app.config["NGN_PER_GHS"] = Decimal("140")
    reply = send("yes")
    assert "rate has been updated" in reply and "GH₵357.14" in reply
    app.config["QUOTE_TTL_MINUTES"] = 15
    assert "Pay *₦50,500.00*" in send("yes")


def test_twilio_signature_enforced(app):
    app.config.update(VALIDATE_TWILIO_SIGNATURE=True, TWILIO_AUTH_TOKEN="tok")
    resp = app.test_client().post("/whatsapp", data={"From": "whatsapp:+1", "Body": "hi"})
    assert resp.status_code == 403
