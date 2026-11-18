"""Flutterwave provider against a fake Flutterwave API (no network)."""
import json
import warnings
from decimal import Decimal

import pytest
import requests

from app import create_app
from database import Transfer, TransferStatus

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

PHONE = "+2348012345678"
HASH = "dashboard-secret-hash"
BASE = "https://api.flutterwave.test/v3"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeFlutterwave:
    """Routes (method, path) to canned responses and records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self.route("GET", "/transfers/rates", {"data": {
            "rate": 132, "source": {"currency": "NGN", "amount": 132000},
            "destination": {"currency": "GHS", "amount": 1000},
        }})
        self.route("POST", "/charges", {"message": "Charge initiated", "meta": {"authorization": {
            "transfer_reference": "MockFLWRef", "transfer_account": "0067100155",
            "transfer_bank": "Mock Bank", "account_expiration": "2026-11-18 23:59:59",
            "transfer_note": "Mock note", "transfer_amount": "50500.00", "mode": "banktransfer",
        }}})
        self.route("POST", "/transfers", {"message": "Transfer Queued Successfully", "data": {
            "id": 555, "status": "NEW", "reference": "x",
        }})

    def route(self, method, path, body, status_code=200, status="success"):
        if isinstance(body, dict):
            body = {"status": status, **body}
        self.routes[(method, path)] = (status_code, body)

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = url[len(BASE):]
        self.calls.append({"method": method, "path": path, "params": params, "json": json, "headers": headers})
        if (method, path) not in self.routes:
            raise AssertionError(f"Unexpected Flutterwave call {method} {path}")
        status_code, body = self.routes[(method, path)]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(status_code, body)

    def calls_to(self, method, path):
        return [c for c in self.calls if c["method"] == method and c["path"] == path]


@pytest.fixture
def fw():
    return FakeFlutterwave()


@pytest.fixture
def app(tmp_path, fw):
    app = create_app({
        "DATABASE_URL": f"sqlite:///{tmp_path}/test.db",
        "VALIDATE_TWILIO_SIGNATURE": False,
        "TWILIO_ACCOUNT_SID": None,
        "TWILIO_AUTH_TOKEN": None,
        "PAYMENT_PROVIDER": "flutterwave",
        "FLUTTERWAVE_BASE_URL": BASE,
        "FLUTTERWAVE_SECRET_KEY": "FLWSECK_TEST-x",
        "FLUTTERWAVE_WEBHOOK_HASH": HASH,
        "CUSTOMER_EMAIL": "payments@example.com",
        "FLUTTERWAVE_DEBIT_CURRENCY": "NGN",
        "FLUTTERWAVE_GH_BANKS": json.dumps({"GCB": {"code": "GH040100", "branch": "GH040101"}}),
        "FEE_PERCENT": Decimal("1"),
        "FX_MARGIN_PERCENT": Decimal("0"),
        "MIN_AMOUNT_NGN": Decimal("1000"),
        "MAX_AMOUNT_NGN": Decimal("1000000"),
    })
    app.extensions["collector"].http = fw
    return app


@pytest.fixture
def send(app):
    client = app.test_client()

    def _send(body):
        resp = client.post("/whatsapp", data={"From": f"whatsapp:{PHONE}", "Body": body})
        assert resp.status_code == 200, resp.data
        return resp.get_data(as_text=True)

    return _send


def transfers(app):
    with app.app_context():
        with app.extensions["db"].Session() as s:
            return s.query(Transfer).all()


def start_momo_transfer(send):
    send("hi"); send("1"); send("50000"); send("yes")
    send("1")  # MTN
    assert "full name" in send("0241234567")
    assert "KWAME MENSAH" in send("kwame  mensah")
    return send("yes")


def webhook(app, event, event_id, verif_hash=HASH):
    body = json.dumps({"event": event, "data": {"id": event_id, "tx_ref": "spoofable", "status": "successful"}})
    headers = {"Content-Type": "application/json"}
    if verif_hash is not None:
        headers["verif-hash"] = verif_hash
    return app.test_client().post("/webhooks/flutterwave", data=body, headers=headers)


def pay(app, fw, reference, amount="50500.00", status="successful", currency="NGN"):
    fw.route("GET", "/transactions/9001/verify", {"data": {
        "id": 9001, "tx_ref": reference, "amount": amount, "currency": currency, "status": status,
    }})
    return webhook(app, "charge.completed", 9001)


def settle(app, fw, reference, status="SUCCESSFUL"):
    fw.route("GET", "/transfers/555", {"data": {
        "id": 555, "reference": f"{reference}-PAYOUT", "status": status, "complete_message": "Bank declined",
    }})
    return webhook(app, "transfer.completed", 555)


# ---------- startup ----------

def test_missing_settings_fail_fast(tmp_path):
    with pytest.raises(ValueError, match="FLUTTERWAVE_SECRET_KEY"):
        create_app({"DATABASE_URL": f"sqlite:///{tmp_path}/x.db", "PAYMENT_PROVIDER": "flutterwave",
                    "FLUTTERWAVE_SECRET_KEY": ""})


def test_unmapped_banks_are_hidden(send):
    send("hi"); send("1"); send("50000")
    menu = send("yes")
    assert "GCB Bank" in menu and "MTN Mobile Money" in menu
    assert "Ecobank" not in menu and "Access" not in menu
    assert "Test mode" not in send("menu")


# ---------- happy path ----------

def test_full_flow(app, fw, send):
    reply = start_momo_transfer(send)
    assert "0067100155" in reply and "Mock Bank" in reply and "2026-11-18 23:59:59" in reply

    [charge] = fw.calls_to("POST", "/charges")
    [t] = transfers(app)
    assert charge["params"] == {"type": "bank_transfer"}
    assert charge["json"]["tx_ref"] == t.reference
    assert charge["json"]["amount"] == "50500.00" and charge["json"]["currency"] == "NGN"
    assert charge["headers"]["Authorization"] == "Bearer FLWSECK_TEST-x"

    assert "confirmed" in send("paid")  # real mode: replying PAID doesn't move money
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT

    assert pay(app, fw, t.reference).status_code == 200
    [payout] = fw.calls_to("POST", "/transfers")
    assert payout["json"] == {
        "amount": "378.78", "currency": "GHS", "debit_currency": "NGN",
        "beneficiary_name": "KWAME MENSAH", "narration": f"9SEND {t.reference}",
        "reference": f"{t.reference}-PAYOUT", "account_bank": "MTN", "account_number": "233241234567",
    }
    t = transfers(app)[0]
    assert t.status == TransferStatus.PAYOUT_PENDING and t.provider_ref == "555"

    assert settle(app, fw, t.reference).status_code == 200
    assert transfers(app)[0].status == TransferStatus.COMPLETED
    assert "complete" in send("any news?")


def test_bank_payout_uses_branch_code(app, fw, send):
    send("hi"); send("1"); send("50000"); send("yes")
    send("4")  # GCB Bank (4th in the filtered list)
    send("1234567890"); send("Ama Owusu"); send("yes")
    [t] = transfers(app)
    pay(app, fw, t.reference)
    [payout] = fw.calls_to("POST", "/transfers")
    assert payout["json"]["account_bank"] == "GH040100"
    assert payout["json"]["destination_branch_code"] == "GH040101"
    assert payout["json"]["account_number"] == "1234567890"


def test_rate_comes_from_flutterwave_and_is_cached(fw, send):
    send("hi")
    assert "GH₵1 = ₦132.00" in send("2")
    send("2")
    assert len(fw.calls_to("GET", "/transfers/rates")) == 1


# ---------- webhook security ----------

@pytest.mark.parametrize("verif_hash", [None, "", "wrong"])
def test_webhook_requires_hash(app, fw, send, verif_hash):
    start_momo_transfer(send)
    [t] = transfers(app)
    fw.route("GET", "/transactions/9001/verify", {"data": {
        "id": 9001, "tx_ref": t.reference, "amount": "50500", "currency": "NGN", "status": "successful"}})
    assert webhook(app, "charge.completed", 9001, verif_hash=verif_hash).status_code == 401
    assert not fw.calls_to("POST", "/transfers")


def test_webhook_is_reverified_with_api(app, fw, send):
    """A webhook claiming success is ignored if Flutterwave's API says otherwise."""
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference, status="failed")
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT
    assert not fw.calls_to("POST", "/transfers")


def test_wrong_currency_ignored(app, fw, send):
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference, currency="USD")
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT


def test_duplicate_charge_webhook_pays_out_once(app, fw, send):
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference)
    pay(app, fw, t.reference)
    assert len(fw.calls_to("POST", "/transfers")) == 1


def test_underpayment_needs_review(app, fw, send):
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference, amount="20000")
    assert transfers(app)[0].status == TransferStatus.NEEDS_REVIEW
    assert not fw.calls_to("POST", "/transfers")


def test_verify_outage_asks_flutterwave_to_retry(app, fw, send):
    start_momo_transfer(send)
    fw.route("GET", "/transactions/9001/verify", requests.ConnectionError("down"))
    assert webhook(app, "charge.completed", 9001).status_code == 503


# ---------- payout outcomes ----------

def test_payout_rejected_is_failed(app, fw, send):
    fw.route("POST", "/transfers", {"message": "Insufficient balance"}, status_code=400, status="error")
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference)
    t = transfers(app)[0]
    assert t.status == TransferStatus.FAILED and "Insufficient balance" in t.failure_reason


def test_payout_timeout_needs_review(app, fw, send):
    fw.route("POST", "/transfers", requests.Timeout("timed out"))
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference)
    assert transfers(app)[0].status == TransferStatus.NEEDS_REVIEW


def test_payout_failure_webhook(app, fw, send):
    start_momo_transfer(send)
    [t] = transfers(app)
    pay(app, fw, t.reference)
    settle(app, fw, t.reference, status="FAILED")
    t = transfers(app)[0]
    assert t.status == TransferStatus.FAILED and t.failure_reason == "Bank declined"


def test_charge_failure_lets_user_retry(app, fw, send):
    fw.route("POST", "/charges", {"message": "Service unavailable"}, status_code=503, status="error")
    send("hi"); send("1"); send("50000"); send("yes"); send("1"); send("0241234567"); send("Kwame Mensah")
    assert "try again" in send("yes")
    assert transfers(app)[0].status == TransferStatus.CANCELLED
    fw.__init__()  # Flutterwave recovers
    assert "0067100155" in send("yes")


def test_rate_outage_message(fw, send):
    fw.route("GET", "/transfers/rates", requests.ConnectionError("down"))
    send("hi"); send("1")
    assert "can't get an exchange rate" in send("50000")


def test_invalid_name_reprompts(send):
    send("hi"); send("1"); send("50000"); send("yes"); send("1"); send("0241234567")
    assert "first and last name" in send("K")
    assert "first and last name" in send("12345 678")
