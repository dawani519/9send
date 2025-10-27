# app.py
from flask import Flask, request, abort
from twilio.twiml.messaging_response import MessagingResponse
from moonpay import create_payment_link, convert_to_usdc, payout_to_ghana, verify_webhook_signature
from database import get_session, save_session
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "9SEND WhatsApp Bot is LIVE! Send a message to +1 415 523 8886", 200

GHANA_BANKS = {
    "1": {"name": "Access Bank", "code": "GH044"},
    "2": {"name": "MTN MoMo", "code": "GH001"}
}

POOL_ACCOUNT = {
    "bank": "Zenith Bank",
    "account": "2214567890",
    "name": "9SEND POOL"
}

@app.route("/moonpay-webhook", methods=["POST"])
def moonpay_webhook():
    signature = request.headers.get("Moonpay-Signature")
    if not verify_webhook_signature(request.data, signature):
        abort(400)
    data = request.json
    if data.get("type") == "payment.succeeded":
        pass  # Future: trigger payout
    return "", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "").strip().lower()
    from_number = request.values.get("From").replace("whatsapp:", "")
    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number) or {"step": "welcome", "data": {}}
    step = session["step"]
    data = session["data"]

    # === WELCOME ===
    if step == "welcome":
        save_session(from_number, "main_menu")
        msg.body("Welcome to *9SEND* – Send money to Ghana in seconds!")
        msg.quick_reply("Send Money", "send")
        msg.quick_reply("Check Rates", "rates")

    # === MAIN MENU ===
    elif step == "main_menu":
        if incoming_msg == "send":
            save_session(from_number, "select_country")
            msg.body("Select destination:")
            msg.quick_reply("Ghana (GHS)", "ghana")
        elif incoming_msg == "rates":
            msg.body("Current Rate: 1 NGN ≈ 0.065 GHS")
            msg.quick_reply("Back", "back")
        else:
            msg.body("Tap a button:")
            msg.quick_reply("Send Money", "send")
            msg.quick_reply("Check Rates", "rates")

    # === SELECT COUNTRY ===
    elif step == "select_country":
        if incoming_msg == "ghana":
            save_session(from_number, "enter_amount")
            msg.body("Enter amount in NGN:\n(e.g., 5000)")
        else:
            msg.body("Tap Ghana:")
            msg.quick_reply("Ghana (GHS)", "ghana")

    # === ENTER AMOUNT ===
    elif step == "enter_amount":
        try:
            amount = float(incoming_msg)
            if amount < 100:
                msg.body("Minimum is ₦100. Try again:")
                return str(resp)
            ghs = round(amount * 0.065, 2)
            fee = round(amount * 0.01, 2)
            total = amount + fee

            save_session(from_number, "confirm_amount", {
                "amount_ngn": amount, "amount_ghs": ghs, "fee": fee, "total": total
            })

            msg.body(f"Send ₦{amount} → ₵{ghs} GHS\nFee: ₦{fee}\nTotal: ₦{total}\n\nConfirm?")
            msg.quick_reply("Confirm", "confirm")
            msg.quick_reply("Cancel", "cancel")
        except:
            msg.body("Enter numbers only (e.g., 5000):")

    # === CONFIRM AMOUNT ===
    elif step == "confirm_amount":
        if incoming_msg == "confirm":
            save_session(from_number, "enter_account")
            msg.body("Enter receiver's account number:\n(e.g., 0690000034)")
        elif incoming_msg == "cancel":
            save_session(from_number, "welcome")
            msg.body("Cancelled.")
            msg.quick_reply("Send Money", "send")
        else:
            msg.body("Tap Confirm or Cancel:")
            msg.quick_reply("Confirm", "confirm")
            msg.quick_reply("Cancel", "cancel")

    # === ENTER ACCOUNT ===
    elif step == "enter_account":
        account = incoming_msg.strip()
        if len(account) < 8:
            msg.body("Invalid account. Try again:")
            return str(resp)
        save_session(from_number, "select_bank", {"account": account})
        msg.body(f"Select bank for {account}:")
        msg.quick_reply("Access Bank", "1")
        msg.quick_reply("MTN MoMo", "2")

    # === SELECT BANK ===
    elif step == "select_bank":
        if incoming_msg in GHANA_BANKS:
            bank = GHANA_BANKS[incoming_msg]
            save_session(from_number, "show_name", {
                "bank_code": bank["code"], "bank_name": bank["name"]
            })
            msg.body(f"Account Name: *JOHN DOE*\nBank: {bank['name']}\n\nProceed?")
            msg.quick_reply("Yes", "yes")
            msg.quick_reply("No", "no")
        else:
            msg.body("Tap a bank:")
            msg.quick_reply("Access Bank", "1")
            msg.quick_reply("MTN MoMo", "2")

    # === SHOW NAME & POOL ===
    elif step == "show_name":
        if incoming_msg == "yes":
            total = data["total"]
            ref = f"9SEND-{from_number[-4:]}"
            save_session(from_number, "awaiting_payment", {"ref": ref})
            msg.body(f"Pay ₦{total} to:\nBank: {POOL_ACCOUNT['bank']}\nAccount: {POOL_ACCOUNT['account']}\nName: {POOL_ACCOUNT['name']}\nRef: {ref}\n\nTap 'I Paid'")
            msg.quick_reply("I Paid", "paid")
        elif incoming_msg == "no":
            save_session(from_number, "welcome")
            msg.body("Cancelled.")
            msg.quick_reply("Send Money", "send")
        else:
            msg.body("Tap Yes or No:")
            msg.quick_reply("Yes", "yes")
            msg.quick_reply("No", "no")

    # === AWAITING PAYMENT ===
    elif step == "awaiting_payment":
        if incoming_msg == "paid":
            usdc = convert_to_usdc(data["amount_ngn"])
            payout = payout_to_ghana(usdc, data["account"], data["bank_code"])
            save_session(from_number, "welcome")
            msg.body(f"Success!\n₦{data['amount_ngn']} → {usdc} USDC → {payout['amount_ghs']} GHS\nSent to JOHN DOE\nDelivered in 8 seconds!")
            msg.quick_reply("Send Again", "send")
        else:
            msg.body("Tap 'I Paid':")
            msg.quick_reply("I Paid", "paid")

    # === FALLBACK ===
    else:
        save_session(from_number, "welcome")
        msg.body("Start over:")
        msg.quick_reply("Send Money", "send")
        msg.quick_reply("Check Rates", "rates")

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True)