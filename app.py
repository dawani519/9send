### 9send/app.py
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
from utils import parse_message
from account import resolve_account
from kyc import verify_kyc_with_images, handle_kyc_upload
from remittance import process_remittance
import psycopg2
import os

app = Flask(__name__)

# Database (Render PostgreSQL)
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS sessions (user_id TEXT PRIMARY KEY, step TEXT, data JSONB)')
conn.commit()

@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    resp = MessagingResponse()
    user_id = request.values.get('From')
    msg = request.values.get('Body', '').strip()
    media_count = int(request.values.get('NumMedia', 0))
    
    # Get session
    cur.execute("SELECT step, data FROM sessions WHERE user_id = %s", (user_id,))
    session = cur.fetchone()
    step = session[0] if session else 'start'
    data = session[1] if session else {}
    
    # KYC Step
    if step == 'kyc_pending' and media_count == 2:
        # Mock approval
        approved, message = verify_kyc_with_images("id", "selfie")
        if approved:
            step = 'verify_account'
            resp.message("KYC Approved! Now send: 'Send 5000 NGN to Ghana 1234567890 Access'")
        else:
            resp.message("KYC failed. Try again.")
    elif step == 'start' and 'kyc' in msg.lower():
        step = 'kyc_pending'
        resp.message("Send your ID photo first, then selfie.")
    
    # Remittance Step
    elif step in ['start', 'verify_account']:
        parsed = parse_message(msg)
        if parsed:
            name = resolve_account(parsed['account'], '044')  # Mock bank code
            step = 'confirm'
            data = parsed
            data['name'] = name
            resp.message(f"Account: {name}. Correct? Reply 'Yes'")
        else:
            resp.message("Format: Send 5000 NGN to Ghana 1234567890 Access")
    
    elif step == 'confirm' and msg.lower() == 'yes':
        collection, usdc, payout = process_remittance(data['amount'], data['currency'], data)
        ghs = payout['amount_ghs']
        resp.message(f"Pay {data['amount']} {data['currency']} → {ghs} GHS sent in 8s! Link: {collection['payment_link']}")
        step = 'start'
        data = {}
    
    else:
        resp.message("Reply 'KYC' to start 9SEND")
    
    # Save session
    cur.execute("""
        INSERT INTO sessions (user_id, step, data) 
        VALUES (%s, %s, %s) 
        ON CONFLICT (user_id) DO UPDATE SET step = %s, data = %s
    """, (user_id, step, str(data), step, str(data)))
    conn.commit()
    
    return str(resp)

if __name__ == '__main__':
    app.run(debug=True)