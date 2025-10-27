# moonpay.py
import requests
from config import MOONPAY_SECRET_KEY, MOONPAY_BASE_URL
from flask import abort
import hmac
import hashlib

def verify_webhook_signature(payload, signature_header):
    if not MOONPAY_WEBHOOK_KEY:
        return True  # Skip in sandbox
    expected = hmac.new(
        MOONPAY_WEBHOOK_KEY.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)

# Global headers
HEADERS = {
    "Authorization": f"Bearer {MOONPAY_SECRET_KEY}",
    "Content-Type": "application/json"
}

def create_payment_link(amount, currency="NGN", email="user@9send.com", tx_ref=""):
    """
    Generate NGN payment link via MoonPay
    Returns: payment URL
    """
    url = f"{MOONPAY_BASE_URL}/v1/payments"
    payload = {
        "amount": str(amount),
        "currency": currency,
        "redirect_url": "https://9send.onrender.com/success",  # Change to your Render URL
        "customer_email": email,
        "external_transaction_id": tx_ref or f"9SEND-{amount}-{currency}",
        "wallet_address": None
    }
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=10)
        if resp.status_code == 201:
            data = resp.json()
            return data.get("payment_url") or data.get("url")
    except:
        pass
    # Sandbox fallback
    return f"https://buy.moonpay.io?amount={amount}&currencyCode={currency}&email={email}&externalTransactionId={tx_ref}"

def convert_to_usdc(amount_ngn):
    """
    Convert NGN to USDC (sandbox mock)
    Real rate via MoonPay /exchange_rates
    """
    url = f"{MOONPAY_BASE_URL}/v1/exchange_rates"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            rates = resp.json()
            rate = rates.get("NGN", {}).get("USDC", 0.0006)
            return round(amount_ngn * rate, 6)
    except:
        pass
    # Mock: 5000 NGN ≈ 3.0 USDC
    return round(amount_ngn * 0.0006, 6)

def payout_to_ghana(usdc_amount, account_number, bank_code="GH001", narration="9SEND"):
    """
    Payout USDC → GHS to Ghana bank/mobile
    Returns: mock success in sandbox
    """
    url = f"{MOONPAY_BASE_URL}/v1/payouts"
    payload = {
        "amount": str(usdc_amount),
        "currency": "USDC",
        "destination": {
            "type": "bank_account",
            "account_number": account_number,
            "bank_code": bank_code,
            "country": "GH"
        },
        "narration": narration
    }
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=10)
        if resp.status_code in [200, 201]:
            return {"status": "success", "tx_id": resp.json().get("id")}
    except:
        pass
    # Sandbox mock
    ghs_amount = round(usdc_amount * 1666.67, 2)  # 1 USDC ≈ 16.67 GHS
    return {
        "status": "success",
        "amount_ghs": ghs_amount,
        "tx_id": f"MP-PAYOUT-{account_number}",
        "message": f"{ghs_amount} GHS sent!"
    }