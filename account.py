### 9send/account.py
import requests
from config import FLUTTERWAVE_SECRET_KEY

def resolve_account(account_number, bank_code, country='NG'):
    """Flutterwave test: 0690000034 + 044 → PASTOR BRIGHT"""
    url = 'https://api.fl  utterwave.com/v3/accounts/resolve'
    headers = {
        'Authorization': f'Bearer {FLUTTERWAVE_SECRET_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'account_number': account_number,
        'account_bank': bank_code
    }
    try:
        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                return data['data']['account_name']
    except:
        pass
    return "JOHN DOE"  # Mock for Zecathon