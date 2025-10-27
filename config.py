### 9send/config.py
import os
from dotenv import load_dotenv

load_dotenv()

MOONPAY_PUBLIC_KEY = os.getenv('MOONPAY_PUBLIC_KEY')  # pk_test_xxx
MOONPAY_SECRET_KEY = os.getenv('MOONPAY_SECRET_KEY')  # sk_test_xxx
MOONPAY_BASE_URL = "https://api.moonpay.io"
MOONPAY_WEBHOOK_KEY = os.getenv('MOONPAY_WEBHOOK_KEY')

# Twilio
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WH_SID = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+19788008705')

# Flutterwave (Test Mode)
FLUTTERWAVE_SECRET_KEY = os.getenv('FLUTTERWAVE_SECRET_KEY', 'FLWSECK_TEST-XXXXXXXXXXXXXXXXXXXXXXXXX')
FLUTTERWAVE_PUBLIC_KEY = os.getenv('FLUTTERWAVE_PUBLIC_KEY', 'FLWPUBK_TEST-XXXXXXXXXXXXXXXXXXXXXXXXX')

# SumSub (Free Trial)
#SUMSUB_API_KEY = os.getenv('SUMSUB_API_KEY', 'test_key_123')
#SUMSUB_BASE_URL = 'https://test-api.sumsub.com'

# Chipper Cash (Sandbox)


# Database
DATABASE_URL = os.getenv('DATABASE_URL')  # Render auto-provides