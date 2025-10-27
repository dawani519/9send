# app.py
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from moonpay import create_payment_link, convert_to_usdc, payout_to_ghana
from database import get_session, save_session
import os

app = Flask(__name__)

# Mock data
GHANA_BANKS = {
    "1": {"name": "Access Bank", "code": "GH044"},
    "2": {"name": "MTN MoMo", "code": "GH001"}
}

POOL_ACCOUNT = {
    "bank": "Zenith Bank",
    "account": "2214567890",
    "name": "9SEND POOL"
}

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From").replace("whatsapp:", "")
    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number) or {"step": "welcome", "data": {}}
    step = session["step"]
    data = session["data"]

    # === WELCOME MENU ===
    if step == "welcome":
        save_session(from_number, "main_menu")
        msg.body(
            "Welcome to *9SEND* – Send money to Ghana in seconds!\n\n"
            "Choose an option:\n"
            "[1] Send Money\n"
            "[2] Check Rates\n"
            "[Reply with number]"
        )

    # === MAIN MENU ===
    elif step == "main_menu":
        if incoming_msg == "1":
            save_session(from_number, "select_country")
            msg.body(
                "Select destination:\n"
                "[1] Ghana (GHS)\n"
                "[Reply 1]"
            )
        elif incoming_msg == "2":
            msg.body("Current Rate: 1 NGN ≈ 0.065 GHS\nReply anything to go back.")
            save_session(from_number, "welcome")
        else:
            msg.body("Please reply *1* or *2*")

    # === SELECT COUNTRY ===
    elif step == "select_country":
        if incoming_msg == "1":
            save_session(from_number, "enter_amount")
            msg.body("Enter amount to send in NGN:\n(e.g., 5000)")
        else:
            msg.body("Please reply *1* for Ghana")

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
                "amount_ngn": amount,
                "amount_ghs": ghs,
                "fee": fee,
                "total": total
            })

            msg.body(
                f"You send: *₦{amount}*\n"
                f"Receiver gets: *₵{ghs} GHS*\n"
                f"Fee: *₦{fee} (1%)*\n"
                f"*Total: ₦{total}*\n\n"
                f"Reply *Confirm* to continue"
            )
        except:
            msg.body("Invalid amount. Enter numbers only (e.g., 5000):")

    # === CONFIRM AMOUNT ===
    elif step == "confirm_amount" and incoming_msg.lower() == "confirm":
        save_session(from_number, "enter_account")
        msg.body("Enter receiver's account number:\n(e.g., 0690000034)")

    # === ENTER ACCOUNT ===
    elif step == "enter_account":
        account = incoming_msg.strip()
        if len(account) < 8:
            msg.body("Invalid account. Try again:")
            return str(resp)
        save_session(from_number, "select_bank", {"account": account})
        banks_list = "\n".join([f"[{k}] {v['name']}" for k, v in GHANA_BANKS.items()])
        msg.body(f"Select bank for {account}:\n{banks_list}\n[Reply 1 or 2]")

    # === SELECT BANK ===
    elif step == "select_bank":
        if incoming_msg in GHANA_BANKS:
            bank = GHANA_BANKS[incoming_msg]
            save_session(from_number, "show_name", {
                "bank_code": bank["code"],
                "bank_name": bank["name"]
            })
            msg.body(
                f"Account Name: *JOHN DOE*\n"
                f"Bank: {bank['name']}\n\n"
                f"Reply *Yes* to proceed"
            )
        else:
            msg.body("Invalid selection. Reply *1* or *2*")

    # === SHOW NAME & POOL ===
    elif step == "show_name" and incoming_msg.lower() == "yes":
        total = data["total"]
        ref = f"9SEND-{from_number[-4:]}"
        save_session(from_number, "awaiting_payment", {"ref": ref})

        msg.body(
            f"Pay *₦{total}* to:\n"
            f"Bank: *{POOL_ACCOUNT['bank']}*\n"
            f"Account: *{POOL_ACCOUNT['account']}*\n"
            f"Name: *{POOL_ACCOUNT['name']}*\n"
            f"Ref: *{ref}*\n\n"
            f"After payment, reply *paid*"
        )

    # === PAID → PAYOUT ===
    elif step == "awaiting_payment" and incoming_msg.lower() == "paid":
        usdc = convert_to_usdc(data["amount_ngn"])
        payout = payout_to_ghana(
            usdc_amount=usdc,
            account_number=data["account"],
            bank_code=data["bank_code"]
        )

        save_session(from_number, "welcome")  # Reset

        msg.body(
            f"Success!\n"
            f"₦{data['amount_ngn']} → {usdc} USDC → {payout['amount_ghs']} GHS\n"
            f"Sent to *JOHN DOE* ({data['bank_name']})\n"
            f"Ref: {payout['tx_id']}\n"
            f"Delivered in 8 seconds!"
        )

    # === FALLBACK ===
    else:
        save_session(from_number, "welcome")
        msg.body("Reply anything to start over.")

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True)