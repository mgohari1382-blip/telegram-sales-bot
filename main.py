import os, asyncio, threading
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import bot_storage
from payment_service import PaymentService, GatewayError

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
ADMIN_ID=int(os.getenv("TELEGRAM_ADMIN_ID","0") or 0)
PORT=int(os.getenv("PORT","8080"))
payment=PaymentService()
web=Flask(__name__)

@web.get("/")
def home(): return "Bot is running."

@web.get("/health")
def health(): return "ok"

@web.get("/payment/callback")
def payment_callback():
    authority=request.args.get("Authority","").strip()
    status=request.args.get("Status","").strip()
    if not authority: return "Invalid callback",400
    try:
        result=payment.handle_callback(authority,status)
        return f"<html><body dir='rtl'><h2>{result}</h2><p>می‌توانید به تلگرام برگردید.</p></body></html>"
    except Exception:
        return "<html><body dir='rtl'><h2>خطا در بررسی پرداخت</h2></body></html>",500

def is_admin(update): return bool(ADMIN_ID and update.effective_user and update.effective_user.id==ADMIN_ID)

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    bot_storage.remember_user(u.id,u.username,u.first_name)
    kb=[[InlineKeyboardButton("🟢 خرید",callback_data="buy")],
        [InlineKeyboardButton("🪪 احراز هویت",callback_data="verify"),
         InlineKeyboardButton("📞 پشتیبانی",callback_data="support")]]
    await update.message.reply_text("سلام 👋\nبه ربات فروش خوش آمدید.",reply_markup=InlineKeyboardMarkup(kb))

async def menu_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="verify":
        u=q.from_user; status=bot_storage.request_verification(u.id,u.username,u.first_name)
        if is_admin(update): pass
        await q.message.reply_text("درخواست احراز هویت ثبت شد. پس از بررسی ادمین به شما اطلاع داده می‌شود." if status=="pending" else "حساب شما قبلاً تأیید شده است.")
    elif q.data=="support":
        await q.message.reply_text("برای پشتیبانی، پیام خود را همینجا ارسال کنید.")
    elif q.data=="buy":
        if bot_storage.get_verification_status(q.from_user.id)!="approved":
            await q.message.reply_text("ابتدا باید احراز هویت شما توسط ادمین تأیید شود.")
            return
        products=bot_storage.list_products(True)
        if not products:
            await q.message.reply_text("فعلاً محصولی برای فروش ثبت نشده است.")
            return
        kb=[[InlineKeyboardButton(f"{p['name']} — {p['price']} {p['currency']}",callback_data=f"product:{p['product_id']}")] for p in products]
        await q.message.reply_text("محصول موردنظر را انتخاب کنید:",reply_markup=InlineKeyboardMarkup(kb))
    elif q.data.startswith("product:"):
        pid=int(q.data.split(":")[1])
        try: order=bot_storage.create_order(pid,q.from_user.id)
        except Exception:
            await q.message.reply_text("ساخت سفارش ممکن نیست. وضعیت احراز هویت و محصول را بررسی کنید.")
            return
        try:
            url=payment.create_payment(order)
        except Exception as e:
            await q.message.reply_text(f"فعلاً ساخت لینک پرداخت ممکن نیست.\n{e}")
            return
        kb=[[InlineKeyboardButton("💳 پرداخت آنلاین",url=url)]]
        await q.message.reply_text(
            f"🧾 سفارش: {order['order_number']}\n"
            f"محصول: {order['product_name']}\n"
            f"مبلغ: {order['price']} {order['currency']}\n\n"
            "برای پرداخت روی دکمه زیر بزنید.",reply_markup=InlineKeyboardMarkup(kb))

async def products(update,context):
    if not is_admin(update): return
    ps=bot_storage.list_products()
    if not ps: await update.message.reply_text("محصولی ثبت نشده."); return
    await update.message.reply_text("\n".join(f"#{p['product_id']} | {p['name']} | {p['price']} {p['currency']} | {'فعال' if p['active'] else 'غیرفعال'}" for p in ps))

async def product_add(update,context):
    if not is_admin(update): return
    raw=update.message.text.partition(" ")[2]
    try: name,details,price,currency=[x.strip() for x in raw.split("|")]
    except ValueError:
        await update.message.reply_text("فرمت:\n/product_add نام | توضیحات | مبلغ | IRR"); return
    try: p=bot_storage.add_product(name,details,price,currency)
    except Exception as e: await update.message.reply_text(f"خطا: {e}"); return
    await update.message.reply_text(f"محصول #{p['product_id']} اضافه شد.")

async def product_toggle(update,context):
    if not is_admin(update): return
    parts=update.message.text.split()
    if len(parts)!=2: await update.message.reply_text("مثال: /product_disable 1"); return
    try: pid=int(parts[1])
    except: await update.message.reply_text("شناسه نامعتبر."); return
    active=parts[0].endswith("enable")
    await update.message.reply_text("انجام شد." if bot_storage.set_product_active(pid,active) else "محصول پیدا نشد.")

async def pending(update,context):
    if not is_admin(update): return
    rows=bot_storage.list_pending_users()
    await update.message.reply_text("\n".join(f"{r['user_id']} | @{r['username'] or '-'} | {r['first_name'] or '-'}" for r in rows) if rows else "درخواستی نیست.")

async def approve(update,context):
    if not is_admin(update): return
    try: uid=int(context.args[0])
    except: await update.message.reply_text("مثال: /approve 123456789"); return
    await update.message.reply_text("تأیید شد." if bot_storage.set_verification_status(uid,"approved") else "کاربر پیدا نشد.")

async def reject(update,context):
    if not is_admin(update): return
    try: uid=int(context.args[0])
    except: await update.message.reply_text("مثال: /reject 123456789"); return
    await update.message.reply_text("رد شد." if bot_storage.set_verification_status(uid,"rejected") else "کاربر پیدا نشد.")

async def myid(update,context): await update.message.reply_text(str(update.effective_user.id))

async def run_bot():
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("products",products))
    app.add_handler(CommandHandler("product_add",product_add))
    app.add_handler(CommandHandler(["product_disable","product_enable"],product_toggle))
    app.add_handler(CommandHandler("pending",pending))
    app.add_handler(CommandHandler("approve",approve))
    app.add_handler(CommandHandler("reject",reject))
    app.add_handler(CommandHandler("id",myid))
    app.add_handler(CallbackQueryHandler(menu_callback))
    await app.initialize(); await app.start(); await app.updater.start_polling()
    await asyncio.Event().wait()

def main():
    if not TOKEN: raise SystemExit("TELEGRAM_BOT_TOKEN تنظیم نشده.")
    if not ADMIN_ID: raise SystemExit("TELEGRAM_ADMIN_ID تنظیم نشده.")
    bot_storage.initialize_database()
    threading.Thread(target=lambda:web.run(host="0.0.0.0",port=PORT),daemon=True).start()
    asyncio.run(run_bot())

if __name__=="__main__": main()
