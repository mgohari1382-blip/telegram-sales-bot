import os, requests, bot_storage
from payment_gateway import ZarinPalGateway, GatewayError

class PaymentService:
    def __init__(self):
        self.gateway=ZarinPalGateway()
        self.bot_token=os.getenv("TELEGRAM_BOT_TOKEN","")
        self.admin_id=os.getenv("TELEGRAM_ADMIN_ID","")
        self.base_url=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
    def _send(self,chat_id,text):
        if not self.bot_token or not chat_id: return
        try:
            requests.post(f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                          json={"chat_id":chat_id,"text":text},timeout=15)
        except Exception:
            pass
    def create_payment(self,order):
        if not self.base_url.startswith("https://"):
            raise GatewayError("PUBLIC_BASE_URL باید آدرس HTTPS عمومی پروژه باشد.")
        callback=f"{self.base_url}/payment/callback"
        authority,url=self.gateway.request_payment(
            order["price"],f"پرداخت سفارش {order['order_number']}",callback)
        bot_storage.attach_authority(order["order_number"],authority)
        return url
    def handle_callback(self,authority,status):
        order=bot_storage.get_order_by_authority(authority)
        if not order: return "سفارش پیدا نشد."
        if status!="OK":
            bot_storage.apply_payment(order["order_number"],"zarinpal",authority,"failed")
            self._send(order["user_id"],f"پرداخت سفارش {order['order_number']} لغو یا ناموفق شد.")
            return "پرداخت لغو شد."
        ok,tx=self.gateway.verify_payment(authority,order["price"])
        if not ok: return "تأیید پرداخت ناموفق بود."
        updated,changed=bot_storage.apply_payment(order["order_number"],"zarinpal",tx,"paid")
        if changed:
            self._send(updated["user_id"],f"✅ پرداخت سفارش {updated['order_number']} با موفقیت تأیید شد.")
            self._send(self.admin_id,
                "💰 پرداخت موفق\n"
                f"شماره سفارش: {updated['order_number']}\n"
                f"مبلغ: {updated['price']} {updated['currency']}\n"
                f"شناسه کاربر: {updated['user_id']}\n"
                f"شناسه تراکنش: {updated['payment_transaction_id']}")
            bot_storage.mark_order_admin_notified(updated["order_number"])
        return "پرداخت با موفقیت تأیید شد."
