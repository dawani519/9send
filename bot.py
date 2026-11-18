"""The WhatsApp conversation. handle_message(phone, text) returns the reply text.

Each step has a handler. Invalid input re-asks the same question; "menu" or
"cancel" work from anywhere.
"""
from datetime import timedelta

from flask import current_app

from banks import BANKS_BY_CODE, bank_from_choice, bank_menu, normalize_account, normalize_name
from database import TransferStatus, UserSession, as_utc, get_db, utcnow
from payments import ProviderError, get_collector, get_payout
from rates import Quote, ghs, make_quote, ngn, parse_amount
from transfers import (
    cancel_transfer,
    confirm_payment,
    create_transfer,
    get_transfer,
    latest_transfer,
    payment_details,
    status_message,
)

RESET_WORDS = {"menu", "start", "restart", "home", "hi", "hello", "hey", "cancel", "stop"}
YES = {"yes", "y", "confirm", "ok", "okay", "yeah", "yep"}
NO = {"no", "n", "nope", "back"}

RATE_UNAVAILABLE = "Sorry, we can't get an exchange rate right now. Please try again in a few minutes."


# ---------- session state ----------

def load_state(phone):
    with get_db().Session() as s:
        row = s.get(UserSession, phone)
        if row is None:
            return "main_menu", {}
        timeout = timedelta(minutes=current_app.config["SESSION_TIMEOUT_MINUTES"])
        if row.updated_at and utcnow() - as_utc(row.updated_at) > timeout:
            return "main_menu", {}
        return row.step, dict(row.data or {})


def save_state(phone, step, data=None):
    with get_db().Session.begin() as s:
        row = s.get(UserSession, phone)
        if row is None:
            s.add(UserSession(phone=phone, step=step, data=data or {}))
        else:
            row.step = step
            row.data = dict(data or {})  # new object so SQLAlchemy sees the change
            row.updated_at = utcnow()


# ---------- messages ----------

def menu_text():
    text = (
        "Welcome to *9SEND* – send money from Nigeria 🇳🇬 to Ghana 🇬🇭\n\n"
        "[1] Send money\n"
        "[2] Today's rate\n"
        "[3] My last transfer\n\n"
        "Reply with a number. Send *menu* at any time to start over."
    )
    if get_payout().is_sandbox:
        text += "\n\n_Test mode: no real money moves._"
    return text


def amount_prompt():
    cfg = current_app.config
    return (
        "How much do you want to send, in naira?\n"
        f"Minimum {ngn(cfg['MIN_AMOUNT_NGN'])}, maximum {ngn(cfg['MAX_AMOUNT_NGN'])}.\n"
        "(e.g. 50000)"
    )


def quote_lines(q: Quote):
    return (
        f"You send: *{ngn(q.amount_ngn)}*\n"
        f"Fee ({current_app.config['FEE_PERCENT']}%): *{ngn(q.fee_ngn)}*\n"
        f"*Total to pay: {ngn(q.total_ngn)}*\n"
        f"Receiver gets: *{ghs(q.amount_ghs)}*"
    )


def new_quote(amount):
    return make_quote(amount, get_payout().ngn_per_ghs(), current_app.config)


def banks():
    return get_payout().supported_banks()


def bank_prompt():
    return f"Where should the money go?\n{bank_menu(banks())}\n\nReply with a number."


def account_prompt(bank):
    if bank.kind == "momo":
        return f"Enter the receiver's {bank.name} number:\n(e.g. 0241234567)"
    return f"Enter the receiver's {bank.name} account number:"


def recipient_text(data):
    bank = BANKS_BY_CODE[data["bank_code"]]
    return (
        f"Receiver: *{data['account_name']}*\n"
        f"{bank.name}: *{data['account_number']}*\n\n"
        f"{quote_lines(Quote.from_dict(data['quote']))}\n\n"
        "Reply *YES* to confirm, or *NO* to change the receiver."
    )


def payment_instructions(t):
    if t.pay_url:
        return payment_details(t) + "\n\nWe'll message you as soon as your payment is confirmed. Reply *CANCEL* to cancel."
    return (
        payment_details(t)
        + "\n\n⚠️ Send the exact amount so we can match your payment.\n"
        "Reply *PAID* once you've sent it, or *CANCEL* to cancel."
    )


# ---------- step handlers ----------

def main_menu(phone, text, data):
    if text == "1":
        save_state(phone, "enter_amount")
        return amount_prompt()
    if text == "2":
        cfg = current_app.config
        try:
            sample = new_quote(cfg["MIN_AMOUNT_NGN"] * 10)
        except ProviderError:
            return RATE_UNAVAILABLE
        return (
            f"Today's rate: GH₵1 = {ngn(sample.ngn_per_ghs)}\n"
            f"Fee: {cfg['FEE_PERCENT']}%\n\n"
            f"Example: send {ngn(sample.amount_ngn)} → receiver gets {ghs(sample.amount_ghs)}\n\n"
            "Reply *1* to send money."
        )
    if text == "3":
        t = latest_transfer(phone)
        return status_message(t) if t else "You haven't made any transfers yet. Reply *1* to send money."
    save_state(phone, "main_menu")
    return menu_text()


def enter_amount(phone, text, data):
    cfg = current_app.config
    amount = parse_amount(text)
    if amount is None:
        return "Please enter the amount as a number, e.g. *50000*."
    if amount < cfg["MIN_AMOUNT_NGN"]:
        return f"The minimum is {ngn(cfg['MIN_AMOUNT_NGN'])}. Enter a larger amount:"
    if amount > cfg["MAX_AMOUNT_NGN"]:
        return f"The maximum is {ngn(cfg['MAX_AMOUNT_NGN'])}. Enter a smaller amount:"
    try:
        quote = new_quote(amount)
    except ProviderError:
        return RATE_UNAVAILABLE
    save_state(phone, "confirm_amount", {"quote": quote.to_dict()})
    return quote_lines(quote) + "\n\nReply *YES* to continue or *NO* to cancel."


def confirm_amount(phone, text, data):
    answer = text.lower()
    if answer in YES:
        save_state(phone, "select_bank", data)
        return bank_prompt()
    if answer in NO:
        save_state(phone, "main_menu")
        return "Cancelled.\n\n" + menu_text()
    return "Reply *YES* to continue or *NO* to cancel."


def select_bank(phone, text, data):
    bank = bank_from_choice(banks(), text)
    if bank is None:
        return "Please choose one of these:\n" + bank_menu(banks())
    save_state(phone, "enter_account", {"quote": data["quote"], "bank_code": bank.code})
    return account_prompt(bank)


def enter_account(phone, text, data):
    bank = BANKS_BY_CODE[data["bank_code"]]
    account = normalize_account(bank, text)
    if account is None:
        hint = "a 10-digit number starting with 0" if bank.kind == "momo" else "10 to 16 digits"
        return f"That doesn't look right. A {bank.name} number is {hint}. Try again:"

    provider = get_payout()
    if not provider.can_resolve_names:
        save_state(phone, "enter_name", {**data, "account_number": account})
        return "Enter the receiver's full name, exactly as registered on the account:"

    try:
        name = provider.resolve_account_name(bank, account)
    except ProviderError:
        return "Sorry, we couldn't check that account right now. Please send the number again in a moment:"
    if not name:
        return f"We couldn't find {account} at {bank.name}. Check the number and try again:"
    data = {**data, "account_number": account, "account_name": name}
    save_state(phone, "confirm_recipient", data)
    return recipient_text(data)


def enter_name(phone, text, data):
    name = normalize_name(text)
    if name is None:
        return "Please enter the receiver's first and last name (letters only):"
    data = {**data, "account_name": name}
    save_state(phone, "confirm_recipient", data)
    return recipient_text(data)


def confirm_recipient(phone, text, data):
    answer = text.lower()
    bank = BANKS_BY_CODE[data["bank_code"]]
    if answer in NO:
        save_state(phone, "enter_account", {"quote": data["quote"], "bank_code": bank.code})
        return account_prompt(bank)
    if answer not in YES:
        return "Reply *YES* to confirm, or *NO* to change the receiver."

    quote = Quote.from_dict(data["quote"])
    ttl = timedelta(minutes=current_app.config["QUOTE_TTL_MINUTES"])
    if utcnow() - as_utc(quote.created_at) > ttl:
        try:
            fresh = new_quote(quote.amount_ngn)
        except ProviderError:
            return RATE_UNAVAILABLE
        data = {**data, "quote": fresh.to_dict()}
        save_state(phone, "confirm_recipient", data)
        return "The exchange rate has been updated.\n\n" + recipient_text(data)

    try:
        t = create_transfer(phone, quote, bank, data["account_number"], data["account_name"])
    except ProviderError:
        return "Sorry, we couldn't set up your payment right now. Reply *YES* to try again, or *menu* to start over."
    save_state(phone, "awaiting_payment", {"reference": t.reference})
    return payment_instructions(t)


def awaiting_payment(phone, text, data):
    t = get_transfer(data.get("reference", ""))
    if t is None:
        save_state(phone, "main_menu")
        return menu_text()
    if t.status != TransferStatus.AWAITING_PAYMENT:
        # Moved on (by webhook) since the last message.
        save_state(phone, "main_menu")
        return status_message(t)
    if text.lower() == "paid":
        if get_collector().is_sandbox:
            # No bank to watch in test mode: simulate the payment arriving.
            t = confirm_payment(t.reference, t.total_ngn, notify=False) or get_transfer(t.reference)
            save_state(phone, "main_menu")
            return status_message(t)
        return (
            "Thanks! We'll message you here as soon as your payment is confirmed. "
            "This usually takes a few minutes."
        )
    return payment_instructions(t)


HANDLERS = {
    "main_menu": main_menu,
    "enter_amount": enter_amount,
    "confirm_amount": confirm_amount,
    "select_bank": select_bank,
    "enter_account": enter_account,
    "enter_name": enter_name,
    "confirm_recipient": confirm_recipient,
    "awaiting_payment": awaiting_payment,
}


def handle_message(phone: str, text: str) -> str:
    text = (text or "").strip()
    step, data = load_state(phone)

    if text.lower() in RESET_WORDS:
        prefix = ""
        if step == "awaiting_payment" and text.lower() in {"cancel", "stop"}:
            if cancel_transfer(data.get("reference", "")):
                prefix = f"Transfer *{data['reference']}* cancelled. Don't send any payment for it.\n\n"
        save_state(phone, "main_menu")
        return prefix + menu_text()

    handler = HANDLERS.get(step)
    if handler is None:  # unknown step from an older version of the bot
        save_state(phone, "main_menu")
        return menu_text()
    return handler(phone, text, data)
