# app.py
from flask import Flask, request, abort
from twilio.twiml.messaging_response import MessagingResponse
from moonpay import create_payment_link, convert_to_usdc, payout_to_ghana, verify_webhook_signature
from database import get_session, save_session
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "9SEND WhatsApp Bot is LIVE!", 200

GHANA_BANKS = {"1": {"name": "Access Bank", "code": "GH044"}, "2": {"name": "MTN MoMo", "code": "GH001"}}
POOL_ACCOUNT = {"bank": "Zenith Bank", "account": "2214567890", "name": "9SEND POOL"}

@app.route("/moonpay-webhook", methods=["POST"])
def moonpay_webhook():
    if not verify_webhook_signature(request.data, request.headers.get("Moonpay-Signature")):
        abort(400)
    return "", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "").strip().lower()
    button_value = request.values.get("ButtonPayload", "").lower()  # For buttons
    from_number = request.values.get("From").replace("whatsapp:", "")
    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number) or {"step": "welcome", "data": {}}
    step = session["step"]
    data = session["data"]
    payload = button_value or incoming_msg  # Use button or text

    # === WELCOME ===
    if step == "welcome":
        save_session(from_number, "main_menu")
        msg.body("Welcome to *9SEND* – Send money to Ghana in seconds!")
        msg.body("Choose:")
        msg.body("", action="/whatsapp", method="POST")
        msg.body("Send Money", type="button", value="send")
        msg.body("Check Rates", type="button", value="rates")

    # === MAIN MENU ===
    elif step == "main_menu":
        if payload == "send":
            save_session(from_number, "select_country")
            msg.body("Select destination:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Ghana (GHS)", type="button", value="ghana")
        elif payload == "rates":
            msg.body("Rate: 1 NGN ≈ 0.065 GHS")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Back", type="button", value="back")
        else:
            msg.body("Tap a button:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Send Money", type="button", value="send")
            msg.body("Check Rates", type="button", value="rates")

    # === SELECT COUNTRY ===
    elif step == "select_country":
        if payload == "ghana":
            save_session(from_number, "enter_amount")
            msg.body("Enter amount in NGN:\n(e.g., 5000)")
        else:
            msg.body("Tap Ghana:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Ghana (GHS)", type="button", value="ghana")

    # === ENTER AMOUNT ===
    elif step == "enter_amount":
        try:
            amount = float(incoming_msg)
            if amount < 100:
                msg.body("Minimum ₦100. Try again:")
                return str(resp)
            ghs = round(amount * 0.065, 2)
            fee = round(amount * 0.01, 2)
            total = amount + fee
            save_session(from_number, "confirm_amount", {"amount_ngn": amount, "amount_ghs": ghs, "fee": fee, "total": total})
            msg.body(f"Send ₦{amount} → ₵{ghs} GHS\nFee: ₦{fee}\nTotal: ₦{total}")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Confirm", type="button", value="confirm")
            msg.body("Cancel", type="button", value="cancel")
        except:
            msg.body("Enter numbers only:")

    # === CONFIRM AMOUNT ===
    elif step == "confirm_amount":
        if payload == "confirm":
            save_session(from_number, "enter_account")
            msg.body("Enter receiver's account number:")
        elif payload == "cancel":
            save_session(from_number, "welcome")
            msg.body("Cancelled.")
        else:
            msg.body("Tap Confirm or Cancel:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Confirm", type="button", value="confirm")
            msg.body("Cancel", type="button", value="cancel")

    # === ENTER ACCOUNT ===
    elif step == "enter_account":
        account = incoming_msg.strip()
        if len(account) < 8:
            msg.body("Invalid account. Try again:")
            return str(resp)
        save_session(from_number, "select_bank", {"account": account})
        msg.body(f"Select bank for {account}:")
        msg.body("", action="/whatsapp", method="POST")
        msg.body("Access Bank", type="button", value="1")
        msg.body("MTN MoMo", type="button", value="2")

    # === SELECT BANK ===
    elif step == "select_bank":
        if payload in GHANA_BANKS:
            bank = GHANA_BANKS[payload]
            save_session(from_number, "show_name", {"bank_code": bank["code"], "bank_name": bank["name"]})
            msg.body(f"Name: *JOHN DOE*\nBank: {bank['name']}\nProceed?")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Yes", type="button", value="yes")
            msg.body("No", type="button", value="no")
        else:
            msg.body("Tap a bank:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Access Bank", type="button", value="1")
            msg.body("MTN MoMo", type="button", value="2")

    # === SHOW NAME & POOL ===
    elif step == "show_name":
        if payload == "yes":
            total = data["total"]
            ref = f"9SEND-{from_number[-4:]}"
            save_session(from_number, "awaiting_payment", {"ref": ref})
            msg.body(f"Pay ₦{total} to:\nBank: {POOL_ACCOUNT['bank']}\nAccount: {POOL_ACCOUNT['account']}\nName: {POOL_ACCOUNT['name']}\nRef: {ref}")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("I Paid", type="button", value="paid")
        elif payload == "no":
            save_session(from_number, "welcome")
            msg.body("Cancelled.")
        else:
            msg.body("Tap Yes or No:")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("Yes", type="button", value="yes")
            msg.body("No", type="button", value="no")

    # === AWAITING PAYMENT ===
    elif step == "awaiting_payment":
        if payload == "paid":
            usdc = convert_to_usdc(data["amount_ngn"])
            payout = payout_to_ghana(usdc, data["account"], data["bank_code"])
            save_session(from_number, "welcome")
            msg.body(f"Success!\n₦{data['amount_ngn']} → {usdc} USDC → {payout['amount_ghs']} GHS\nDelivered in 8 seconds!")
        else:
            msg.body("Tap 'I Paid':")
            msg.body("", action="/whatsapp", method="POST")
            msg.body("I Paid", type="button", value="paid")

    # === FALLBACK ===
    else:
        save_session(from_number, "welcome")
        msg.body("Start over:")
        msg.body("", action="/whatsapp", method="POST")
        msg.body("Send Money", type="button", value="send")

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True)