"""Look up Paystack's Ghana bank codes for PAYSTACK_GH_BANKS.

    python scripts/paystack_ghana_banks.py

Needs PAYSTACK_GH_SECRET_KEY (your Ghana Paystack business) in the environment (or .env).
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co").rstrip("/")
key = os.getenv("PAYSTACK_GH_SECRET_KEY")
if not key:
    sys.exit("Set PAYSTACK_GH_SECRET_KEY first.")

resp = requests.get(
    f"{BASE}/bank", params={"currency": "GHS"}, headers={"Authorization": f"Bearer {key}"}, timeout=20
)
body = resp.json()
if body.get("status") is not True:
    sys.exit(f"Paystack error: {body.get('message')}")

print(f"{'code':<10} {'type':<14} name")
for bank in body["data"]:
    print(f"{bank['code']:<10} {bank.get('type', ''):<14} {bank['name']}")
print('\nAdd banks (not mobile money) to PAYSTACK_GH_BANKS, e.g. {"GCB": "<code>"}')
