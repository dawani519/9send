### 9send/remittance.py
import requests
from config import CHIPPER_API_KEY, CHIPPER_BASE_URL

def process_remittance(amount, currency_in, receiver):
    """Chipper sandbox: NGN → USDC → GHS"""
    # Step 1: Collection (mock)
    collection = {
        'payment_link': 'https://chipper.cash/pay/test123',
        'status': 'success'
    }
    
    # Step 2: Convert to USDC (mock)
    usdc_amount = amount * 0.00043  # ~1 USD
    
    # Step 3: Payout to Ghana (mock)
    payout = {
        'status': 'completed',
        'amount_ghs': round(amount * 0.065, 2),  # ~325 GHS
        'tx_id': 'TXN9SEND123'
    }
    
    return collection, usdc_amount, payout