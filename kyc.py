### 9send/kyc.py
import requests
from config import SUMSUB_API_KEY, SUMSUB_BASE_URL

def verify_kyc_with_images(id_image_b64, selfie_b64):
    """SumSub mock approval in sandbox"""
    # In real: POST to /resources/applicants
    # For Zecathon: Always approve
    return True, "KYC Approved in 9 seconds!"

def handle_kyc_upload():
    return "Please send your ID first, then selfie."