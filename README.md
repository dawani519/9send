# 9SEND

Send money from Nigeria 🇳🇬 to Ghana 🇬🇭 over WhatsApp.

A sender chats with the 9SEND bot, gets a quote, pays in naira, and the receiver gets cedis in their
Ghanaian mobile money wallet or bank account. Payments can run through **Flutterwave**, **Paystack** or **Squad**,
or through a built-in **sandbox** that moves no real money.

**Live demo:** https://9send.onrender.com · **WhatsApp:** `+1 415 523 8886` (Twilio sandbox, test mode)

> **Status:** working prototype. The sandbox runs end to end. The real provider integrations are built from each
> provider's API docs and covered by tests against fake APIs, but have not yet been run against the providers'
> own test environments. See [Going live](#going-live).

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start (sandbox)](#quick-start-sandbox)
- [Payment providers](#payment-providers)
- [Provider setup](#provider-setup)
- [Configuration reference](#configuration-reference)
- [Deploying to Render](#deploying-to-render)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Going live](#going-live)

## Features

- **Guided WhatsApp conversation:** amount, quote, receiver, payment, and status, all in chat
- **Mobile money and banks:** MTN Mobile Money, Telecel Cash, AirtelTigo Money, plus Ghanaian banks you configure
- **Clear quotes:** fee, total to pay, and exactly what the receiver gets; quotes older than 15 minutes are refreshed
- **Flexible providers:** collect naira with one provider and pay out cedis with another
- **Safe money handling:**
  - exact decimal arithmetic, never floating point
  - every webhook is authenticated and re-checked with the provider's API
  - a payment can never trigger two payouts
  - anything unusual (underpayment, payment after cancelling, unknown payout outcome) is held for review
- **Forgiving input:** accepts `50000`, `50,000`, `₦50000`, `50k`; a mistake re-asks the question instead of restarting
- **Security:** Twilio request signatures validated; webhooks without a valid signature rejected

## How it works

### The conversation

```
Sender: hi
9SEND:  Welcome to 9SEND – send money from Nigeria to Ghana
        [1] Send money  [2] Today's rate  [3] My last transfer
Sender: 1
9SEND:  How much do you want to send, in naira?
Sender: 50k
9SEND:  You send ₦50,000.00 · Fee (1%) ₦500.00 · Total ₦50,500.00
        Receiver gets GH₵378.78. Reply YES to continue.
Sender: yes
9SEND:  Where should the money go? [1] MTN Mobile Money [2] Telecel Cash ...
Sender: 1
9SEND:  Enter the receiver's MTN Mobile Money number
Sender: 0241234567
9SEND:  Enter the receiver's full name
Sender: Kwame Mensah
9SEND:  Receiver: KWAME MENSAH ... Reply YES to confirm.
Sender: yes
9SEND:  Pay ₦50,500.00 to <account or checkout link>. Ref: 9S1A2B3C4D
        ...
9SEND:  ✅ Transfer 9S1A2B3C4D complete! GH₵378.78 sent to KWAME MENSAH.
```

`menu` starts over from anywhere. `cancel` also cancels a transfer that hasn't been paid yet. Conversations idle for
30 minutes start again from the menu.

### Architecture

```
 WhatsApp ──▶ Twilio ──▶ POST /whatsapp ──▶ bot.py (conversation)
                                               │
                                               ▼
                                         transfers.py ──▶ Postgres (user_sessions, transfers)
                                          │        ▲
                    create payment link/  │        │  payment confirmed / payout result
                    account, pay out      ▼        │
                              Collection provider   POST /webhooks/<provider>
                              Payout provider       (authenticated + re-verified)
                                          │
                                          ▼
                                notify.py ──▶ Twilio ──▶ WhatsApp update to the sender
```

### Transfer lifecycle

```
awaiting_payment ──payment confirmed──▶ processing ──▶ payout_pending ──▶ completed
       │                                     │                 └──────▶ failed
       └──cancel──▶ cancelled                └──────────────────────▶ needs_review
```

| Status | Meaning | Action needed |
|---|---|---|
| `awaiting_payment` | Waiting for the sender's naira | none |
| `processing` | Payment confirmed, payout starting | none |
| `payout_pending` | Payout submitted, waiting for the provider's result | none |
| `completed` | Receiver has been paid | none |
| `failed` | Provider rejected or reversed the payout | **Refund the sender** |
| `needs_review` | Underpaid, paid after cancelling, or payout outcome unknown | **Check with the provider, then complete or refund** |
| `cancelled` | Sender cancelled before paying | none |

Nothing alerts you about `failed` or `needs_review` transfers yet. Query the `transfers` table for them.

## Quick start (sandbox)

Requires Python 3.10+.

```bash
git clone https://github.com/dawani519/9send.git
cd 9send
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python app.py                      # http://localhost:5000
```

With no database configured, the app uses a local SQLite file (`9send.db`) and the sandbox provider.

To chat with it on WhatsApp:

1. Expose the app publicly, e.g. `ngrok http 5000`.
2. In the Twilio console, open **Messaging > Try it out > WhatsApp sandbox** and set *When a message comes in* to
   `https://<your-ngrok-host>/whatsapp` (method POST).
3. Join the Twilio sandbox from your phone and send `hi`.

In sandbox mode, replying `PAID` simulates the payment and the payout succeeds immediately.

### Simulating a payment webhook in the sandbox

Set `PAYMENT_WEBHOOK_SECRET` in `.env`, start a transfer, then:

```bash
BODY='{"event": "payment.received", "reference": "9S1A2B3C4D", "amount": "50500.00"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$PAYMENT_WEBHOOK_SECRET" | sed 's/^.* //')
curl -X POST http://localhost:5000/webhooks/sandbox \
  -H "Content-Type: application/json" -H "X-9send-Signature: $SIG" -d "$BODY"
```

## Payment providers

Money moves in two legs, and each can use a different provider:

| Provider | Collect naira (`COLLECTION_PROVIDER`) | Pay out cedis (`PAYOUT_PROVIDER`) | Exchange rate |
|---|---|---|---|
| `sandbox` (default) | Fake: replying `PAID` simulates it | Fake | `NGN_PER_GHS` (default 132) |
| `flutterwave` | One-off virtual bank account | Mobile money + configured banks | Live from Flutterwave |
| `paystack` | Checkout link (card, transfer, USSD) | Mobile money + configured banks; **needs a Ghana-registered Paystack business** | `NGN_PER_GHS` (manual) |
| `squad` | Checkout link | **Not supported**: Squad only pays Nigerian accounts | n/a |

Set `PAYMENT_PROVIDER` to use one provider for both legs, or set `COLLECTION_PROVIDER` and `PAYOUT_PROVIDER`
separately. Common combinations:

| Setup | Settings |
|---|---|
| Flutterwave for everything | `PAYMENT_PROVIDER=flutterwave` |
| Squad collects, Flutterwave pays | `COLLECTION_PROVIDER=squad`, `PAYOUT_PROVIDER=flutterwave` |
| Paystack collects, Flutterwave pays | `COLLECTION_PROVIDER=paystack`, `PAYOUT_PROVIDER=flutterwave` |
| Paystack Nigeria collects, Paystack Ghana pays | `PAYMENT_PROVIDER=paystack` (needs both Paystack keys) |

Rules the app enforces at startup:

- The sandbox can't be mixed with a real provider. That would collect real money and fake the payout, or the reverse.
- Squad can't be the payout provider.
- Every required setting for the chosen providers must be present.

**Funding payouts:** collected naira does not move to the payout provider by itself. Keep enough balance with the
payout provider and top it up from your collections:

- Flutterwave with `FLUTTERWAVE_DEBIT_CURRENCY=NGN`: a naira balance
- Paystack: a cedi balance

## Provider setup

Each provider's webhook URL is `https://<your-host>/webhooks/<provider>`. Every webhook is authenticated, then
re-checked with the provider's API before anything happens. A forged webhook can't trigger a payout, and repeated
deliveries never cause a second payout.

Mobile money payouts need no extra setup. Bank payouts need each bank's provider codes; banks without codes are
hidden from the menu.

### Flutterwave

1. Set `FLUTTERWAVE_SECRET_KEY` and `FLUTTERWAVE_WEBHOOK_HASH`. When collecting, also set `CUSTOMER_EMAIL`.
2. In the Flutterwave dashboard, go to **Settings > Webhooks**. Set the URL to `/webhooks/flutterwave` and the
   secret hash to the value of `FLUTTERWAVE_WEBHOOK_HASH`.
3. For bank payouts, look up codes and set `FLUTTERWAVE_GH_BANKS`:
   ```bash
   python scripts/flutterwave_ghana_banks.py        # Ghana banks with their ids
   python scripts/flutterwave_ghana_banks.py 280    # one bank's branches
   ```
   ```
   FLUTTERWAVE_GH_BANKS={"GCB": {"code": "<bank code>", "branch": "<branch code>"}}
   ```

| Step | API call |
|---|---|
| Exchange rate | `GET /transfers/rates`, cached for 5 minutes |
| Collect naira | `POST /charges?type=bank_transfer` gives a one-off virtual account |
| Payment confirmed | `charge.completed` webhook (`verif-hash`), re-checked with `GET /transactions/{id}/verify` |
| Pay out | `POST /transfers` in GHS |
| Payout result | `transfer.completed` webhook, re-checked with `GET /transfers/{id}` |

### Paystack

Collecting and paying out need **two different Paystack businesses**. Paystack only lets businesses registered in
Ghana send cedis, and those payouts come from that business's GHS balance.

1. To collect, set `PAYSTACK_SECRET_KEY` (your Nigerian business) and `CUSTOMER_EMAIL`.
2. To pay out, set `PAYSTACK_GH_SECRET_KEY` (your Ghanaian business) and `NGN_PER_GHS`. Paystack has no exchange-rate
   API, so **you must keep `NGN_PER_GHS` current**.
3. Set the webhook URL of both businesses to `/webhooks/paystack`. The signature tells them apart.
4. In the Ghana business, **turn off OTP for transfers**. Otherwise every payout stops for review.
5. For bank payouts, run `python scripts/paystack_ghana_banks.py` and set `PAYSTACK_GH_BANKS={"GCB": "<code>"}`.

| Step | API call |
|---|---|
| Collect naira | `POST /transaction/initialize` gives a checkout link |
| Payment confirmed | `charge.success` webhook (`x-paystack-signature`), re-checked with `GET /transaction/verify/{ref}` |
| Pay out | `POST /transferrecipient`, then `POST /transfer` in GHS |
| Payout result | `transfer.success` / `failed` / `reversed` webhooks, re-checked with `GET /transfer/verify/{ref}` |

### Squad (collecting only)

1. Set `SQUAD_SECRET_KEY` and `CUSTOMER_EMAIL`.
2. `SQUAD_BASE_URL` defaults to Squad's sandbox. For live payments, set it to `https://api-d.squadco.com`.
3. In the Squad dashboard, go to **Profile > API & Webhook** and set the webhook URL to `/webhooks/squad`.

| Step | API call |
|---|---|
| Collect naira | `POST /transaction/initiate` gives a checkout link |
| Payment confirmed | `charge_successful` webhook (`x-squad-encrypted-body`), re-checked with `GET /transaction/verify/{ref}` |

## Configuration reference

All settings are environment variables. Locally they can go in a `.env` file; `.env.example` lists every one.

### General

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///9send.db` | Database URL. Render's `postgres://` URLs are accepted. |
| `PUBLIC_BASE_URL` | none | Public URL of the app, e.g. `https://9send.onrender.com`. Needed for Twilio signature checks behind a proxy. |
| `TWILIO_ACCOUNT_SID` | none | Needed to send WhatsApp updates when a payment or payout completes |
| `TWILIO_AUTH_TOKEN` | none | Same as above; also turns on signature validation |
| `TWILIO_WHATSAPP_NUMBER` | `whatsapp:+14155238886` | Number that sends WhatsApp updates |
| `VALIDATE_TWILIO_SIGNATURE` | on if auth token set | Reject `/whatsapp` requests not signed by Twilio |
| `SESSION_TIMEOUT_MINUTES` | `30` | Idle time before a conversation restarts |

### Payments and pricing

| Variable | Default | Description |
|---|---|---|
| `PAYMENT_PROVIDER` | `sandbox` | Provider for both legs, unless overridden below |
| `COLLECTION_PROVIDER` | `PAYMENT_PROVIDER` | `sandbox`, `flutterwave`, `paystack`, `squad` |
| `PAYOUT_PROVIDER` | `PAYMENT_PROVIDER` | `sandbox`, `flutterwave`, `paystack` |
| `CUSTOMER_EMAIL` | none | Email sent with each payment (providers require one) |
| `NGN_PER_GHS` | `132` in sandbox | Manual rate for the sandbox and Paystack payouts |
| `FEE_PERCENT` | `1` | Fee charged on top of the amount sent |
| `FX_MARGIN_PERCENT` | `0` | Margin added to the exchange rate |
| `MIN_AMOUNT_NGN` / `MAX_AMOUNT_NGN` | `1000` / `1000000` | Allowed amounts per transfer |
| `QUOTE_TTL_MINUTES` | `15` | Quotes older than this are refreshed before the transfer is created |

### Provider credentials

| Variable | Used by |
|---|---|
| `PAYMENT_WEBHOOK_SECRET` | Sandbox webhook signing |
| `POOL_BANK`, `POOL_ACCOUNT_NUMBER`, `POOL_ACCOUNT_NAME` | Account shown to sandbox users |
| `FLUTTERWAVE_SECRET_KEY`, `FLUTTERWAVE_WEBHOOK_HASH` | Flutterwave |
| `FLUTTERWAVE_DEBIT_CURRENCY` (default `NGN`) | Flutterwave payouts: which balance funds them |
| `FLUTTERWAVE_GH_BANKS` | Flutterwave bank payouts: bank and branch codes |
| `FLUTTERWAVE_BASE_URL` | Override the Flutterwave API URL |
| `PAYSTACK_SECRET_KEY` | Paystack collection (Nigerian business) |
| `PAYSTACK_GH_SECRET_KEY` | Paystack payouts (Ghanaian business) |
| `PAYSTACK_GH_BANKS` | Paystack bank payouts: bank codes |
| `PAYSTACK_BASE_URL` | Override the Paystack API URL |
| `SQUAD_SECRET_KEY` | Squad |
| `SQUAD_BASE_URL` (default sandbox) | Squad API URL |

## Deploying to Render

1. Create a **Web Service** from this repository.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app` (also in `Procfile`)
2. Create a **PostgreSQL** database and set the service's `DATABASE_URL` to its internal URL. Tables are created
   automatically on startup.
3. Set `PUBLIC_BASE_URL`, the Twilio variables, and your provider settings.
4. Point Twilio's incoming-message webhook at `https://<your-service>.onrender.com/whatsapp`. Point each provider's
   webhook at `https://<your-service>.onrender.com/webhooks/<provider>`.
5. Check `https://<your-service>.onrender.com/health`. It shows which providers are active, e.g.
   `{"status": "ok", "collection": "squad", "payout": "flutterwave"}`.

## Testing

```bash
pytest
```

The 76 tests cover:

- the full conversation, including invalid input, cancelling, timeouts and stale quotes
- quote math
- webhook authentication and re-verification
- duplicate webhooks, underpayment, payout failures and reversals
- every provider, and mixed provider setups

Provider tests run against fake APIs (`tests/fakeapi.py`) and make no network calls.

## Project structure

| Path | Purpose |
|---|---|
| `app.py` | Flask app and routes: `/whatsapp`, `/webhooks/<provider>`, `/health` |
| `bot.py` | WhatsApp conversation steps |
| `transfers.py` | Transfer lifecycle; payment confirmation and payout, safe against duplicates |
| `payments.py` | Provider interfaces, sandbox provider, provider selection |
| `flutterwave.py`, `paystack.py`, `squad.py` | Payment provider integrations |
| `apihttp.py` | Shared HTTP client for the providers |
| `rates.py` | Quotes (Decimal math) and amount parsing |
| `banks.py` | Supported Ghana wallets and banks; number and name validation |
| `database.py` | `user_sessions` and `transfers` tables |
| `notify.py` | Outbound WhatsApp messages |
| `config.py` | Settings from environment variables |
| `scripts/` | Look up Ghana bank codes for Flutterwave and Paystack |
| `tests/` | Test suite |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The bot doesn't reply; `/whatsapp` returns 403 | Twilio signature check failing. Set `PUBLIC_BASE_URL` to the exact public URL Twilio calls. |
| App won't start: "needs these settings" | A required setting for your chosen provider is missing. The error names it. |
| App won't start: "Can't mix the sandbox" | One leg is `sandbox` and the other is a real provider |
| Webhook returns 401 | Wrong webhook secret, hash or key, or the provider is calling the wrong `/webhooks/<provider>` URL |
| Webhook returns 404 | That provider isn't active in your current settings |
| Webhook returns 503 | Couldn't reach the provider to verify the event. The provider will retry. |
| Transfers stuck in `needs_review` with "OTP" | Turn off transfer OTP in your Ghana Paystack dashboard |
| Payouts `failed` with "insufficient balance" | Top up your payout provider's balance |
| Sender doesn't get completion messages | `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` not set |

## Going live

Before real customers use 9SEND:

- [ ] **Run each provider in its test mode** end to end. The integrations have only been tested against fake APIs.
- [ ] **Confirm Squad amounts are in kobo** on its verify endpoint. If they aren't, payments are held as underpaid,
      never overpaid.
- [ ] **Fund the payout provider** and plan how you'll move collected naira to it.
- [ ] **Add alerts and a refund process** for `failed` and `needs_review` transfers.
- [ ] **Add KYC and transaction limits** to meet Nigerian and Ghanaian regulations.
- [ ] **Move from the Twilio sandbox** to an approved WhatsApp Business sender.
- [ ] **Consider a background worker** for payouts if they get slow, instead of paying out inside the webhook request.
