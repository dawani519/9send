"""Look up Flutterwave's Ghana bank and branch codes for FLUTTERWAVE_GH_BANKS.

    python scripts/flutterwave_ghana_banks.py              # list Ghana banks
    python scripts/flutterwave_ghana_banks.py <bank id>    # list that bank's branches

Needs FLUTTERWAVE_SECRET_KEY in the environment (or .env).
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("FLUTTERWAVE_BASE_URL", "https://api.flutterwave.com/v3").rstrip("/")


def get(path):
    key = os.getenv("FLUTTERWAVE_SECRET_KEY")
    if not key:
        sys.exit("Set FLUTTERWAVE_SECRET_KEY first.")
    resp = requests.get(BASE + path, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    body = resp.json()
    if body.get("status") != "success":
        sys.exit(f"Flutterwave error: {body.get('message')}")
    return body["data"]


if len(sys.argv) == 1:
    print(f"{'id':>6}  {'code':<12} name")
    for bank in get("/banks/GH"):
        print(f"{bank['id']:>6}  {bank['code']:<12} {bank['name']}")
    print("\nRun again with a bank id to list its branches.")
else:
    print(f"{'branch_code':<12} branch_name")
    for branch in get(f"/banks/{int(sys.argv[1])}/branches"):
        print(f"{branch['branch_code']:<12} {branch['branch_name']}")
    print('\nAdd to FLUTTERWAVE_GH_BANKS, e.g. {"GCB": {"code": "<bank code>", "branch": "<branch_code>"}}')
