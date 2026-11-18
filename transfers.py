"""Transfer lifecycle: create, cancel, confirm payment, pay out, settle."""
import logging
import secrets

from sqlalchemy import select, update

from database import Transfer, TransferStatus, get_db, utcnow
from notify import send_whatsapp
from payments import PaymentReceived, PayoutUpdate, ProviderError, get_collector, get_payout
from rates import ghs, ngn

log = logging.getLogger(__name__)
S = TransferStatus


def new_reference():
    return "9S" + secrets.token_hex(4).upper()


def create_transfer(phone, quote, bank, account_number, account_name) -> Transfer:
    """Create the transfer and the account the sender pays into.

    Raises ProviderError if the payment account can't be created (the transfer is then cancelled).
    """
    transfer = Transfer(
        reference=new_reference(),
        phone=phone,
        status=S.AWAITING_PAYMENT,
        amount_ngn=quote.amount_ngn,
        fee_ngn=quote.fee_ngn,
        total_ngn=quote.total_ngn,
        ngn_per_ghs=quote.ngn_per_ghs,
        amount_ghs=quote.amount_ghs,
        bank_code=bank.code,
        bank_name=bank.name,
        account_number=account_number,
        account_name=account_name,
    )
    with get_db().Session.begin() as s:
        s.add(transfer)

    try:
        c = get_collector().create_collection(transfer)
    except ProviderError as exc:
        log.warning("Could not create payment account for %s: %s", transfer.reference, exc)
        _transition(transfer.reference, S.AWAITING_PAYMENT, status=S.CANCELLED, failure_reason=str(exc)[:255])
        raise
    with get_db().Session.begin() as s:
        t = s.get(Transfer, transfer.reference)
        t.pay_bank_name = c.bank_name
        t.pay_account_number = c.account_number
        t.pay_account_name = c.account_name
        t.pay_amount_ngn = c.amount_ngn
        t.pay_expires = c.expires
        t.pay_url = c.pay_url
    return get_transfer(transfer.reference)


def get_transfer(reference):
    with get_db().Session() as s:
        return s.get(Transfer, reference)


def latest_transfer(phone):
    with get_db().Session() as s:
        return s.scalars(
            select(Transfer).where(Transfer.phone == phone).order_by(Transfer.created_at.desc()).limit(1)
        ).first()


def _transition(reference, from_status, **values):
    """Atomically move a transfer out of from_status. Returns True if this call won."""
    with get_db().Session.begin() as s:
        result = s.execute(
            update(Transfer)
            .where(Transfer.reference == reference, Transfer.status == from_status)
            .values(updated_at=utcnow(), **values)
        )
        return result.rowcount == 1


def cancel_transfer(reference) -> bool:
    return _transition(reference, S.AWAITING_PAYMENT, status=S.CANCELLED)


def handle_event(event):
    if isinstance(event, PaymentReceived):
        return confirm_payment(event.reference, event.amount_ngn)
    if isinstance(event, PayoutUpdate):
        return apply_payout_update(event)
    return None


def confirm_payment(reference, amount_paid, notify=True):
    """Record a confirmed naira payment and start the payout.

    Safe to call repeatedly (e.g. webhook retries): only the first call for a
    transfer that is awaiting payment pays out. Returns the updated transfer,
    or None if nothing was done.
    """
    if not _transition(reference, S.AWAITING_PAYMENT, status=S.PROCESSING, amount_paid_ngn=amount_paid):
        # Money for a transfer the sender already cancelled: someone must refund or complete it.
        if _transition(
            reference, S.CANCELLED, status=S.NEEDS_REVIEW, amount_paid_ngn=amount_paid,
            failure_reason="Paid after the transfer was cancelled",
        ):
            return _finish(reference, notify)
        log.info("Ignoring payment for %s: unknown or not awaiting payment", reference)
        return None

    transfer = get_transfer(reference)
    if amount_paid < transfer.total_ngn:
        _transition(
            reference, S.PROCESSING, status=S.NEEDS_REVIEW,
            failure_reason=f"Underpaid: received {amount_paid}, expected {transfer.total_ngn}",
        )
        return _finish(reference, notify)

    try:
        result = get_payout().payout(transfer)
    except Exception as exc:  # outcome unknown: never leave it silently stuck in "processing"
        log.exception("Payout crashed for %s", reference)
        new_status, values = S.NEEDS_REVIEW, {"failure_reason": f"Payout error: {exc}"[:255]}
    else:
        new_status, values = {
            "success": (S.COMPLETED, {"provider_ref": result.provider_ref}),
            "pending": (S.PAYOUT_PENDING, {"provider_ref": result.provider_ref}),
            "failed": (S.FAILED, {"failure_reason": (result.error or "Payout rejected")[:255]}),
        }.get(result.status, (S.NEEDS_REVIEW, {"failure_reason": f"Payout outcome unknown: {result.error}"[:255]}))
    _transition(reference, S.PROCESSING, status=new_status, **values)
    return _finish(reference, notify)


def apply_payout_update(update_event: PayoutUpdate, notify=True):
    values = {"provider_ref": update_event.provider_ref}
    if update_event.success:
        values["status"] = S.COMPLETED
    else:
        values["status"] = S.FAILED
        values["failure_reason"] = (update_event.error or "Payout failed")[:255]
    if not _transition(update_event.reference, S.PAYOUT_PENDING, **values):
        # A payout reported as successful can later be reversed (the money bounced back).
        if update_event.success or not _transition(update_event.reference, S.COMPLETED, **values):
            log.info("Ignoring payout update for %s: not pending", update_event.reference)
            return None
    return _finish(update_event.reference, notify)


def _finish(reference, notify):
    transfer = get_transfer(reference)
    log.info("Transfer %s is now %s", reference, transfer.status)
    if notify:
        send_whatsapp(transfer.phone, status_message(transfer))
    return transfer


def payment_details(t: Transfer) -> str:
    if t.pay_url:
        return f"Pay *{ngn(t.pay_amount_ngn or t.total_ngn)}* securely here:\n{t.pay_url}\n\nRef: *{t.reference}*"
    lines = [
        f"Pay *{ngn(t.pay_amount_ngn or t.total_ngn)}* to:",
        f"Bank: *{t.pay_bank_name}*",
        f"Account: *{t.pay_account_number}*",
    ]
    if t.pay_account_name:
        lines.append(f"Name: *{t.pay_account_name}*")
    lines.append(f"Ref: *{t.reference}*")
    if t.pay_expires:
        lines.append(f"Pay before: {t.pay_expires}")
    return "\n".join(lines)


def status_message(t: Transfer) -> str:
    receiver = f"{t.account_name} ({t.bank_name})"
    if t.status == S.AWAITING_PAYMENT:
        return f"⏳ Transfer *{t.reference}* is waiting for your payment.\n\n" + payment_details(t)
    if t.status in (S.PROCESSING, S.PAYOUT_PENDING):
        return (
            f"🔄 Payment received for *{t.reference}*. "
            f"We're sending {ghs(t.amount_ghs)} to {receiver} and will message you when it lands."
        )
    if t.status == S.COMPLETED:
        return f"✅ Transfer *{t.reference}* complete!\n*{ghs(t.amount_ghs)}* sent to {receiver}."
    if t.status == S.NEEDS_REVIEW:
        return (
            f"⚠️ We received your payment for *{t.reference}* but need to check something before sending it. "
            "Your money is safe and our team will contact you."
        )
    if t.status == S.FAILED:
        return (
            f"❌ We couldn't complete transfer *{t.reference}* to {receiver}. "
            f"Your {ngn(t.total_ngn)} is safe and our team will contact you about a refund."
        )
    if t.status == S.CANCELLED:
        return f"Transfer *{t.reference}* was cancelled."
    return f"Transfer *{t.reference}*: {t.status}"
