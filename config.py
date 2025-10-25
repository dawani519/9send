### 9send/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Twilio
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WH_SID = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+19788008705')

# Flutterwave (Test Mode)
FLUTTERWAVE_SECRET_KEY = os.getenv('FLUTTERWAVE_SECRET_KEY', 'FLWSECK_TEST-XXXXXXXXXXXXXXXXXXXXXXXXX')
FLUTTERWAVE_PUBLIC_KEY = os.getenv('FLUTTERWAVE_PUBLIC_KEY', 'FLWPUBK_TEST-XXXXXXXXXXXXXXXXXXXXXXXXX')

# SumSub (Free Trial)
SUMSUB_API_KEY = os.getenv('SUMSUB_API_KEY', 'test_key_123')
SUMSUB_BASE_URL = 'https://test-api.sumsub.com'

# Chipper Cash (Sandbox)


# Database
DATABASE_URL = os.getenv('DATABASE_URL')  # Render auto-provides