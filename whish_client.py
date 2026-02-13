import os
import logging
import json
import requests
import copy

logger = logging.getLogger("WhishClient")

class WhishClient:
    def __init__(self):
        self.base_url = os.environ.get("WHISH_BASE_URL", "https://api.sandbox.whish.money/itel-service/api")
        self.channel = os.environ.get("WHISH_CHANNEL")
        self.secret = os.environ.get("WHISH_SECRET")
        self.website_url = os.environ.get("WHISH_WEBSITE_URL")
        self.user_agent = os.environ.get("WHISH_USER_AGENT", "Whish/1.0 (https://whish.money; support@whish.money)")
        
        # Check if critical env vars are missing
        if not all([self.channel, self.secret, self.website_url]):
            logger.warning("[WhishClient] Missing one or more required environment variables: WHISH_CHANNEL, WHISH_SECRET, WHISH_WEBSITE_URL")

    def _get_headers(self):
        return {
            "channel": self.channel,
            "secret": self.secret,
            "websiteUrl": self.website_url,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json"
        }

    def _log_request(self, method, url, payload=None):
        """Logs request details with secrets redacted."""
        safe_payload = None
        if payload:
            safe_payload = copy.deepcopy(payload)
            # Redact sensitive fields if any (Whish payload mostly doesn't have sensitive user info in create call, but good practice)
            # Secrets are in headers, which we don't log fully or at all here
        
        logger.info(f"[WhishClient] Request: {method} {url} | Payload: {json.dumps(safe_payload)}")

    def _log_response(self, response):
        """Logs response details."""
        try:
            content = response.json()
            # Redact if necessary
            logger.info(f"[WhishClient] Response: {response.status_code} | Body: {json.dumps(content)}")
        except:
            logger.info(f"[WhishClient] Response: {response.status_code} | Body: {response.text}")

    def create_payment(self, amount, currency, invoice, external_id, success_callback_url, failure_callback_url, success_redirect_url, failure_redirect_url):
        endpoint = f"{self.base_url}/payment/whish"
        
        payload = {
            "amount": float(amount),
            "currency": currency,
            "invoice": str(invoice),
            "externalId": int(external_id),
            "successCallbackUrl": success_callback_url,
            "failureCallbackUrl": failure_callback_url,
            "successRedirectUrl": success_redirect_url,
            "failureRedirectUrl": failure_redirect_url
        }
        
        self._log_request("POST", endpoint, payload)
        
        try:
            response = requests.post(
                endpoint, 
                headers=self._get_headers(), 
                json=payload,
                timeout=30
            )
            self._log_response(response)
            response.raise_for_status()
            
            data = response.json()
            if not data.get('status', False):
                 logger.error(f"[WhishClient] API returned status=False. Code: {data.get('code')}, Dialog: {data.get('dialog')}")
                 return None, data
                 
            return data.get('data', {}).get('collectUrl'), data
            
        except Exception as e:
            logger.error(f"[WhishClient] Create Payment Failed: {e}")
            return None, {"error": str(e)}

    def check_status(self, external_id, currency):
        endpoint = f"{self.base_url}/payment/collect/status"
        
        payload = {
            "currency": currency,
            "externalId": int(external_id)
        }
        
        self._log_request("POST", endpoint, payload)
        
        try:
            response = requests.post(
                endpoint,
                headers=self._get_headers(),
                json=payload,
                timeout=30
            )
            self._log_response(response)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"[WhishClient] Check Status Failed: {e}")
            return None
