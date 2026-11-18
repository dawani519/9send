"""Paystack and Squad providers, and mixing collection/payout providers (fake APIs, no network)."""
import hashlib
import hmac
import json
import warnings
from decimal import Decimal

import pytest
import requests

from app import create_app
from database import Transfer, TransferStatus
from tests.fakeapi import FakeApi

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

PHONE = "+2348012345678"
PAYSTACK = "https://api.paystack.test"
SQUAD = "https://squad.test"
FLW = "https://flw.test/v3"
PS_NG_KEY = "sk_test_nigeria"
PS_GH_KEY = "sk_test_ghana"
SQUAD_KEY = "sandbox_sk_squad"
FLW_HASH = "flw-hash"


def make_app(tmp_path, collect, payout, **extra):
    cfg = {
        "DATABASE_URL": f"sqlite:///{tmp_path}/test.db",
        "VALIDATE_TWILIO_SIGNATURE": False,
        "TWILIO_ACCOUNT_SID": None,
        "TWILIO_AUTH_TOKEN": None,
        "PAYMENT_PROVIDER": "sandbox",
        "COLLECTION_PROVIDER": collect,
        "PAYOUT_PROVIDER": payout,
        "CUSTOMER_EMAIL": "payments@example.com",
        "NGN_PER_GHS": "132",
        "FEE_PERCENT": Decimal("1"),
        "FX_MARGIN_PERCENT": Decimal("0"),
        "MIN_AMOUNT_NGN": Decimal("1000"),
        "MAX_AMOUNT_NGN": Decimal("1000000"),
        "PAYSTACK_BASE_URL": PAYSTACK,
        "PAYSTACK_SECRET_KEY": PS_NG_KEY,
        "PAYSTACK_GH_SECRET_KEY": PS_GH_KEY,
        "PAYSTACK_GH_BANKS": json.dumps({"GCB": "040100"}),
        "SQUAD_BASE_URL": SQUAD,
        "SQUAD_SECRET_KEY": SQUAD_KEY,
        "FLUTTERWAVE_BASE_URL": FLW,
        "FLUTTERWAVE_SECRET_KEY": "FLWSECK_TEST-x",
        "FLUTTERWAVE_WEBHOOK_HASH": FLW_HASH,
        "FLUTTERWAVE_DEBIT_CURRENCY": "NGN",
        "FLUTTERWAVE_GH_BANKS": "{}",
        **extra,
    }
    app = create_app(cfg)
    fakes = {}
    for role in ("collector", "payout"):
        provider = app.extensions[role]
        base = {"paystack": PAYSTACK, "squad": SQUAD, "flutterwave": FLW}[provider.name]
        provider.http = fakes[role] = FakeApi(base)
    return app, fakes["collector"], fakes["payout"]


def sender(app):
    client = app.test_client()

    def send(body):
        resp = client.post("/whatsapp", data={"From": f"whatsapp:{PHONE}", "Body": body})
        assert resp.status_code == 200, resp.data
        return resp.get_data(as_text=True)

    return send


def transfers(app):
    with app.app_context():
        with app.extensions["db"].Session() as s:
            return s.query(Transfer).all()


def start(send, bank_choice="1", account="0241234567"):
    send("hi"); send("1"); send("50000"); send("yes")
    send(bank_choice)
    send(account)
    send("Kwame Mensah")
    return send("yes")


def post(app, path, payload, headers):
    body = json.dumps(payload).encode()
    return app.test_client().post(path, data=body, headers={"Content-Type": "application/json", **headers(body)})


def paystack_sig(key):
    return lambda body: {"x-paystack-signature": hmac.new(key.encode(), body, hashlib.sha512).hexdigest()}


def squad_sig(key):
    return lambda body: {"x-squad-encrypted-body": hmac.new(key.encode(), body, hashlib.sha512).hexdigest().upper()}


# ---------- fakes for each API ----------

def paystack_collect_routes(api):
    api.route("POST", "/transaction/initialize", {"status": True, "data": {
        "authorization_url": "https://checkout.paystack.com/abc123", "reference": "x", "access_code": "abc123"}})


def paystack_paid(app, api, reference, amount_kobo=5050000, status="success", key=PS_NG_KEY):
    api.route("GET", f"/transaction/verify/{reference}", {"status": True, "data": {
        "reference": reference, "amount": amount_kobo, "currency": "NGN", "status": status}})
    return post(app, "/webhooks/paystack", {"event": "charge.success", "data": {"reference": reference}},
                paystack_sig(key))


def paystack_payout_routes(api, transfer_status="pending"):
    api.route("POST", "/transferrecipient", {"status": True, "data": {"recipient_code": "RCP_test"}})
    api.route("POST", "/transfer", {"status": True, "data": {"status": transfer_status, "transfer_code": "TRF_test"}})


def paystack_transfer_event(app, api, reference, status="success", event="transfer.success"):
    ref = reference.lower() + "-payout"
    api.route("GET", f"/transfer/verify/{ref}", {"status": True, "data": {
        "reference": ref, "status": status, "transfer_code": "TRF_test"}})
    return post(app, "/webhooks/paystack", {"event": event, "data": {"reference": ref}}, paystack_sig(PS_GH_KEY))


def squad_collect_routes(api):
    api.route("POST", "/transaction/initiate", {"status": 200, "message": "success", "data": {
        "checkout_url": "https://sandbox-pay.squadco.com/REF", "transaction_ref": "x"}})


def squad_paid(app, api, reference, amount_kobo=5050000, status="Success", key=SQUAD_KEY):
    api.route("GET", f"/transaction/verify/{reference}", {"status": 200, "message": "success", "data": {
        "transaction_ref": reference, "transaction_amount": amount_kobo,
        "transaction_currency_id": "NGN", "transaction_status": status}})
    return post(app, "/webhooks/squad", {"Event": "charge_successful", "TransactionRef": reference,
                                          "Body": {"amount": amount_kobo}}, squad_sig(key))


def flutterwave_payout_routes(api):
    api.route("GET", "/transfers/rates", {"status": "success", "data": {
        "source": {"currency": "NGN", "amount": 132000}, "destination": {"currency": "GHS", "amount": 1000}}})
    api.route("POST", "/transfers", {"status": "success", "data": {"id": 777, "status": "NEW"}})


# ---------- startup validation ----------

@pytest.mark.parametrize("collect,payout,message", [
    ("paystack", "sandbox", "Can't mix the sandbox"),
    ("sandbox", "flutterwave", "Can't mix the sandbox"),
    ("paystack", "squad", "Squad can't pay out to Ghana"),
    ("stripe", "paystack", "Unknown COLLECTION_PROVIDER"),
])
def test_invalid_combinations(tmp_path, collect, payout, message):
    with pytest.raises(ValueError, match=message):
        make_app(tmp_path, collect, payout)


@pytest.mark.parametrize("setting,collect,payout", [
    ("PAYSTACK_SECRET_KEY", "paystack", "flutterwave"),
    ("PAYSTACK_GH_SECRET_KEY", "squad", "paystack"),
    ("NGN_PER_GHS", "squad", "paystack"),
    ("SQUAD_SECRET_KEY", "squad", "flutterwave"),
    ("CUSTOMER_EMAIL", "squad", "flutterwave"),
])
def test_missing_settings_fail_fast(tmp_path, setting, collect, payout):
    with pytest.raises(ValueError, match=setting):
        make_app(tmp_path, collect, payout, **{setting: ""})


def test_payment_provider_sets_both_legs(tmp_path):
    app, collector_api, payout_api = make_app(tmp_path, "", "", PAYMENT_PROVIDER="flutterwave")
    assert app.extensions["collector"] is app.extensions["payout"]
    assert app.test_client().get("/health").json == {"status": "ok", "collection": "flutterwave",
                                                     "payout": "flutterwave"}


# ---------- Paystack collects, Flutterwave pays out ----------

def test_paystack_collect_flutterwave_payout(tmp_path):
    app, ps, fw = make_app(tmp_path, "paystack", "flutterwave")
    paystack_collect_routes(ps)
    flutterwave_payout_routes(fw)
    send = sender(app)

    reply = start(send)
    assert "https://checkout.paystack.com/abc123" in reply and "₦50,500.00" in reply
    [init] = ps.calls_to("POST", "/transaction/initialize")
    [t] = transfers(app)
    assert init["json"]["amount"] == "5050000"  # kobo
    assert init["json"]["reference"] == t.reference and init["json"]["currency"] == "NGN"
    assert init["headers"]["Authorization"] == f"Bearer {PS_NG_KEY}"

    assert paystack_paid(app, ps, t.reference).status_code == 200
    [payout] = fw.calls_to("POST", "/transfers")
    assert payout["json"]["amount"] == "378.78" and payout["json"]["account_number"] == "233241234567"
    assert transfers(app)[0].status == TransferStatus.PAYOUT_PENDING

    # A duplicate webhook doesn't pay twice.
    paystack_paid(app, ps, t.reference)
    assert len(fw.calls_to("POST", "/transfers")) == 1


def test_paystack_bad_signature_rejected(tmp_path):
    app, ps, fw = make_app(tmp_path, "paystack", "flutterwave")
    paystack_collect_routes(ps)
    flutterwave_payout_routes(fw)
    start(sender(app))
    [t] = transfers(app)
    assert paystack_paid(app, ps, t.reference, key="forged").status_code == 401
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT


def test_paystack_verify_failed_is_ignored(tmp_path):
    app, ps, fw = make_app(tmp_path, "paystack", "flutterwave")
    paystack_collect_routes(ps)
    flutterwave_payout_routes(fw)
    start(sender(app))
    [t] = transfers(app)
    paystack_paid(app, ps, t.reference, status="abandoned")
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT


def test_paystack_underpayment(tmp_path):
    app, ps, fw = make_app(tmp_path, "paystack", "flutterwave")
    paystack_collect_routes(ps)
    flutterwave_payout_routes(fw)
    start(sender(app))
    [t] = transfers(app)
    paystack_paid(app, ps, t.reference, amount_kobo=5050)  # ₦50.50
    assert transfers(app)[0].status == TransferStatus.NEEDS_REVIEW


def test_unused_provider_webhook_404(tmp_path):
    app, ps, fw = make_app(tmp_path, "paystack", "flutterwave")
    assert app.test_client().post("/webhooks/squad", data=b"{}").status_code == 404


# ---------- Squad collects, Paystack (Ghana) pays out ----------

def test_squad_collect_paystack_payout(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    paystack_payout_routes(ps)
    send = sender(app)

    assert "GH₵1 = ₦132.00" in (send("hi") and send("2"))  # manual NGN_PER_GHS rate
    send("menu")
    reply = start(send, bank_choice="2", account="0201234567")  # Telecel Cash
    assert "https://sandbox-pay.squadco.com/REF" in reply
    [t] = transfers(app)
    [init] = sq.calls_to("POST", "/transaction/initiate")
    assert init["json"] == {"amount": 5050000, "email": "payments@example.com", "currency": "NGN",
                            "initiate_type": "inline", "transaction_ref": t.reference}

    assert squad_paid(app, sq, t.reference).status_code == 200
    [recipient] = ps.calls_to("POST", "/transferrecipient")
    assert recipient["json"] == {"type": "mobile_money", "name": "KWAME MENSAH", "account_number": "0201234567",
                                 "bank_code": "VOD", "currency": "GHS"}
    [transfer] = ps.calls_to("POST", "/transfer")
    assert transfer["headers"]["Authorization"] == f"Bearer {PS_GH_KEY}"
    assert transfer["json"]["amount"] == 37878  # pesewas
    assert transfer["json"]["reference"] == t.reference.lower() + "-payout"
    assert transfers(app)[0].status == TransferStatus.PAYOUT_PENDING

    assert paystack_transfer_event(app, ps, t.reference).status_code == 200
    assert transfers(app)[0].status == TransferStatus.COMPLETED
    assert "complete" in send("any news?")


def test_squad_bad_signature_rejected(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    start(sender(app))
    [t] = transfers(app)
    assert squad_paid(app, sq, t.reference, key="forged").status_code == 401


def test_squad_pending_status_ignored(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    start(sender(app))
    [t] = transfers(app)
    squad_paid(app, sq, t.reference, status="Pending")
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT


def test_squad_verify_outage_retries(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    start(sender(app))
    [t] = transfers(app)
    sq.route("GET", f"/transaction/verify/{t.reference}", requests.ConnectionError("down"))
    body = json.dumps({"Event": "charge_successful", "TransactionRef": t.reference}).encode()
    resp = app.test_client().post("/webhooks/squad", data=body, headers=squad_sig(SQUAD_KEY)(body))
    assert resp.status_code == 503


# ---------- Paystack on both legs (two businesses, one webhook URL) ----------

def test_paystack_both_legs(tmp_path):
    app, ps_ng, ps_gh = make_app(tmp_path, "paystack", "paystack")
    assert app.extensions["collector"] is not app.extensions["payout"]
    paystack_collect_routes(ps_ng)
    paystack_payout_routes(ps_gh)
    send = sender(app)
    start(send, bank_choice="4", account="1234567890")  # GCB Bank (mapped)
    [t] = transfers(app)

    # A charge event signed by the Ghana business is not a naira payment.
    ps_gh.route("GET", f"/transaction/verify/{t.reference}", {"status": True, "data": {}})
    paystack_paid(app, ps_ng, t.reference, key=PS_GH_KEY)
    assert transfers(app)[0].status == TransferStatus.AWAITING_PAYMENT

    paystack_paid(app, ps_ng, t.reference)
    [recipient] = ps_gh.calls_to("POST", "/transferrecipient")
    assert recipient["json"]["type"] == "ghipss" and recipient["json"]["bank_code"] == "040100"

    paystack_transfer_event(app, ps_gh, t.reference)
    assert transfers(app)[0].status == TransferStatus.COMPLETED


# ---------- Paystack payout outcomes ----------

@pytest.mark.parametrize("status,expected", [
    ("success", TransferStatus.COMPLETED),
    ("otp", TransferStatus.NEEDS_REVIEW),
    ("failed", TransferStatus.FAILED),
])
def test_paystack_transfer_statuses(tmp_path, status, expected):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    paystack_payout_routes(ps, transfer_status=status)
    start(sender(app))
    [t] = transfers(app)
    squad_paid(app, sq, t.reference)
    t = transfers(app)[0]
    assert t.status == expected
    if status == "otp":
        assert "OTP" in t.failure_reason


def test_paystack_recipient_error_needs_review(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    ps.route("POST", "/transferrecipient", {"status": False, "message": "Invalid account"}, status_code=400)
    start(sender(app))
    [t] = transfers(app)
    squad_paid(app, sq, t.reference)
    t = transfers(app)[0]
    assert t.status == TransferStatus.NEEDS_REVIEW and "Invalid account" in t.failure_reason
    assert not ps.calls_to("POST", "/transfer")


def test_paystack_reversal_after_success(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    squad_collect_routes(sq)
    paystack_payout_routes(ps)
    start(sender(app))
    [t] = transfers(app)
    squad_paid(app, sq, t.reference)
    paystack_transfer_event(app, ps, t.reference)
    assert transfers(app)[0].status == TransferStatus.COMPLETED
    paystack_transfer_event(app, ps, t.reference, status="reversed", event="transfer.reversed")
    t = transfers(app)[0]
    assert t.status == TransferStatus.FAILED and "reversed" in t.failure_reason


def test_paystack_unmapped_banks_hidden(tmp_path):
    app, sq, ps = make_app(tmp_path, "squad", "paystack")
    send = sender(app)
    send("hi"); send("1"); send("50000")
    menu = send("yes")
    assert "GCB Bank" in menu and "Ecobank" not in menu
