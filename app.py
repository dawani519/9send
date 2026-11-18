"""9SEND: send money from Nigeria to Ghana over WhatsApp."""
import logging

from flask import Flask, abort, request
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from werkzeug.middleware.proxy_fix import ProxyFix

from bot import handle_message
from config import Config
from database import Database
from payments import (
    ProviderError,
    ProviderUnavailable,
    WebhookAuthError,
    build_providers,
    get_collector,
    get_payout,
    providers_named,
)
from transfers import handle_event

log = logging.getLogger(__name__)


def create_app(overrides=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # Render terminates TLS in front of us

    app.extensions["db"] = Database(app.config["DATABASE_URL"])
    collector, payout = build_providers(app.config)
    app.extensions["collector"], app.extensions["payout"] = collector, payout
    if payout.is_sandbox:
        log.warning("Running with the SANDBOX payment provider: no real money moves.")
    else:
        log.info("Collecting naira with %s, paying out cedis with %s", collector.name, payout.name)

    @app.get("/")
    def home():
        return "9SEND WhatsApp bot is live.", 200

    @app.get("/health")
    def health():
        return {"status": "ok", "collection": get_collector().name, "payout": get_payout().name}, 200

    @app.post("/whatsapp")
    def whatsapp_webhook():
        if not _twilio_request_is_valid(app):
            abort(403)
        sender = request.values.get("From", "")
        if not sender:
            abort(400)
        phone = sender.replace("whatsapp:", "")
        try:
            reply = handle_message(phone, request.values.get("Body", ""))
        except Exception:
            log.exception("Error handling message from %s", phone)
            reply = "Sorry, something went wrong on our side. Please try again, or send *menu* to start over."
        resp = MessagingResponse()
        resp.message(reply)
        return str(resp), 200, {"Content-Type": "application/xml"}

    @app.post("/webhooks/<provider_name>")
    def payments_webhook(provider_name):
        candidates = providers_named(provider_name)
        if not candidates:
            abort(404)
        body = request.get_data()
        for provider in candidates:  # Paystack can have two (Nigerian and Ghanaian businesses)
            try:
                event = provider.parse_webhook(request.headers, body)
            except WebhookAuthError:
                continue
            except ProviderUnavailable:
                # Couldn't re-verify with the provider; a non-2xx makes it retry later.
                log.exception("Could not verify %s webhook with the provider", provider_name)
                return "", 503
            except ProviderError:
                log.exception("%s rejected webhook verification", provider_name)
                return "", 200
            if event is not None:
                handle_event(event)
            return "", 200
        abort(401)

    return app


def _twilio_request_is_valid(app):
    cfg = app.config
    if not cfg["VALIDATE_TWILIO_SIGNATURE"]:
        return True
    if cfg["PUBLIC_BASE_URL"]:
        url = cfg["PUBLIC_BASE_URL"] + request.path
        if request.query_string:
            url += "?" + request.query_string.decode()
    else:
        url = request.url
    validator = RequestValidator(cfg["TWILIO_AUTH_TOKEN"] or "")
    return validator.validate(url, request.form, request.headers.get("X-Twilio-Signature", ""))


_app = None


def __getattr__(name):
    """Build the app on first access, so `gunicorn app:app` works without
    importing this module (e.g. in tests) creating a database connection."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)


if __name__ == "__main__":
    create_app().run(debug=True)
