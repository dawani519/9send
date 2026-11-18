"""Outbound WhatsApp messages (used when a payment is confirmed by webhook)."""
import logging

from flask import current_app
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

log = logging.getLogger(__name__)


def send_whatsapp(phone: str, body: str) -> bool:
    cfg = current_app.config
    if not (cfg["TWILIO_ACCOUNT_SID"] and cfg["TWILIO_AUTH_TOKEN"]):
        log.warning("Twilio credentials missing; not sending to %s: %s", phone, body)
        return False
    try:
        Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"]).messages.create(
            from_=cfg["TWILIO_WHATSAPP_FROM"], to=f"whatsapp:{phone}", body=body
        )
        return True
    except TwilioRestException:
        log.exception("Failed to send WhatsApp message to %s", phone)
        return False
