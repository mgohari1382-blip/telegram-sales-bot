# Render deployment

این پروژه برای اجرای Python/Flask روی Render آماده شده است.

## تنظیمات Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `python main.py`
- Plan: Free برای تست

## Environment Variables
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_ID`
- `PUBLIC_BASE_URL` = آدرس سرویس Render با https، بدون اسلش آخر
- `ZARINPAL_MERCHANT_ID`
- `ZARINPAL_SANDBOX` = `1` برای تست، `0` برای درگاه واقعی
- `PORT` توسط Render تنظیم می‌شود (در render.yaml روی 10000 قرار داده شده)

## نکته مهم
نسخه فعلی ربات از polling تلگرام استفاده می‌کند. Render در پلن Free سرویس وب را پس از 15 دقیقه بدون ترافیک ورودی متوقف می‌کند؛ بنابراین این نسخه برای تست مناسب است و برای ربات 24/7 باید بعداً webhook یا پلن همیشه‌روشن استفاده شود.
