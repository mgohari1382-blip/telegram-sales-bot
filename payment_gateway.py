import os, requests
from decimal import Decimal

class GatewayError(Exception): pass

class ZarinPalGateway:
    name="zarinpal"
    def __init__(self):
        self.merchant_id=os.getenv("ZARINPAL_MERCHANT_ID","").strip()
        self.sandbox=os.getenv("ZARINPAL_SANDBOX","0")=="1"
        if self.sandbox:
            self.api_base="https://sandbox.zarinpal.com/pg/v4"
            self.start_base="https://sandbox.zarinpal.com/pg/StartPay"
        else:
            self.api_base="https://api.zarinpal.com/pg/v4"
            self.start_base="https://www.zarinpal.com/pg/StartPay"
    def _check(self):
        if not self.merchant_id: raise GatewayError("ZARINPAL_MERCHANT_ID تنظیم نشده است.")
    def request_payment(self, amount, description, callback_url):
        self._check()
        payload={"merchant_id":self.merchant_id,"amount":int(Decimal(str(amount))),
                 "callback_url":callback_url,"description":description}
        r=requests.post(self.api_base+"/payment/request.json",json=payload,timeout=20)
        r.raise_for_status()
        data=r.json()
        code=(data.get("data") or {}).get("code")
        if code != 100:
            err=data.get("errors") or {}
            raise GatewayError(f"خطای زرین‌پال: {err.get('message',code)}")
        authority=data["data"]["authority"]
        return authority,f"{self.start_base}/{authority}"
    def verify_payment(self, authority, amount):
        self._check()
        payload={"merchant_id":self.merchant_id,"amount":int(Decimal(str(amount))),"authority":authority}
        r=requests.post(self.api_base+"/payment/verify.json",json=payload,timeout=20)
        r.raise_for_status()
        data=r.json()
        code=(data.get("data") or {}).get("code")
        if code not in (100,101):
            return False,None
        ref=(data.get("data") or {}).get("ref_id")
        return True,str(ref or authority)
