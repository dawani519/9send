### 9send/utils.py
import re
import logging
from flask import jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def parse_message(text):
    """Parse: 'Send 5000 NGN to Ghana 1234567890 Access'"""
    pattern = r"send\s+(\d+)\s+(\w+)\s+to\s+(\w+)\s+(\d+)\s+(.+)"
    match = re.match(pattern, text.lower())
    if match:
        amount, currency, country, account, bank = match.groups()
        return {
            'amount': int(amount),
            'currency': currency.upper(),
            'country': country.title(),
            'account': account,
            'bank': bank.title()
        }
    return None

def mock_webhook_signature():
    """Mock Chipper webhook HMAC for sandbox"""
    return {'X-Chipper-Signature': 'test_signature'}