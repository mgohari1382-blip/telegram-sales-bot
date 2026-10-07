
"""Full Persian crypto sales/trading Telegram bot.

Everything except the real payment gateway is included:
- Persian main menu
- manual KYC with photo/document collection
- admin approve/reject KYC
- product catalog with price and inventory
- buy flow with order number
- inventory deducted ONLY after confirmed payment
- manual payment confirmation for testing
- sell request flow
- support forwarding and admin replies
- user order history
- admin product/order/sale commands

When a real gateway is added, call bot_storage.mark_order_paid(...) only after
the gateway has verified the transaction server-side.
"""

from __future__ import annotations
import logging, os, re
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)
import bot_storage as store

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL","INFO").upper(), logging.INFO))
logger=logging.getLogger("crypto_bot")

BUY="🟢 خرید ارز"; SELL="🔴 فروش ارز"; KYC="🪪 احراز هویت"; SUPPORT="📞 پشتیبانی"
ORDERS="📦 سفارش‌های من"; CANCEL="❌ لغو"
MENU=ReplyKeyboardMarkup([[BUY,SELL],[KYC,SUPPORT],[ORDERS]],resize_keyboard=True)

BOT_TOKEN_PATTERN=re.compile(r"\b(?:bot)?\d{6,}:[A-Za-z0-9_-]{20,}\b",re.I)

class Redact(logging.Filter):
    def filter(self,record):
        record.msg=BOT_TOKEN_PATTERN.sub("[REDACTED]",record.getMessage()); record.args=()
        return True

for h in logging.getLogger().handlers: h.addFilter(Redact())

def admin_id():
    raw=os.getenv("TELEGRAM_ADMIN_ID","").strip()
    return int(raw) if raw.isdigit() else None

async def private(update):
    c=update.effective_chat
    if c and c.type==ChatType.PRIVATE: return True
    if update.effective_message: await update.effective_message.reply_text("لطفاً در گفت‌وگوی خصوصی ربات ادامه دهید.")
    return False

async def start(update,context):
    if not await private(update): return
    u=update.effective_user; store.remember_user(u.id,u.username,u.first_name)
    await update.effective_message.reply_text(
        "سلام 👋\nبه ربات خرید و فروش ارز خوش آمدید.\n"
        "برای خرید، ابتدا احراز هویت شما باید توسط مدیر تأیید شود.",
        reply_markup=MENU)

async def help_cmd(update,context):
    if not await private(update): return
    text=("راهنما:\n"
          "🟢 خرید ارز: انتخاب محصول و ساخت سفارش\n"
          "🔴 فروش ارز: ثبت درخواست فروش\n"
          "🪪 احراز هویت: ارسال اطلاعات و مدارک برای بررسی مدیر\n"
          "📞 پشتیبانی: ارسال مستقیم پیام برای مدیر\n"
          "📦 سفارش‌های من: مشاهده سفارش‌های قبلی")
    if update.effective_user.id==admin_id():
        text+=("\n\nدستورات مدیر:\n"
               "/products\n/product_add نام | توضیحات | قیمت ریالی | موجودی\n"
               "/product_update شناسه | نام | توضیحات | قیمت ریالی | موجودی\n"
               "/product_disable شناسه\n/product_enable شناسه\n"
               "/pending_kyc\n/pending_orders\n"
               "/pay شماره_سفارش [شناسه_تراکنش]\n/fail شماره_سفارش\n"
               "/pending_sales\n/sale_approve شناسه\n/sale_reject شناسه\n"
               "/reply شناسه_کاربر متن")
    await update.effective_message.reply_text(text,reply_markup=MENU)

async def id_cmd(update,context):
    if not await private(update): return
    await update.effective_message.reply_text(f"شناسه عددی تلگرام شما: {update.effective_user.id}",reply_markup=MENU)

async def require_admin(update):
    if not await private(update): return False
    if update.effective_user.id!=admin_id():
        await update.effective_message.reply_text("این بخش فقط برای مدیر است.")
        return False
    return True

def product_keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{p['name']} | {p['price']} ریال | موجودی {p['stock']}",
                              callback_data=f"buy:{p['product_id']}")]
        for p in rows
    ])

async def buy_menu(update,context):
    if not await private(update): return
    u=update.effective_user
    status=store.get_verification_status(u.id)
    if status!="approved":
        msg={"pending":"درخواست احراز هویت شما هنوز در حال بررسی است.",
             "rejected":"احراز هویت شما رد شده است؛ برای پیگیری با مدیر تماس بگیرید."}.get(
                 status,"برای خرید ابتدا گزینه «احراز هویت» را انتخاب و تأیید مدیر را دریافت کنید.")
        await update.effective_message.reply_text(msg,reply_markup=MENU); return
    ps=store.list_products(True)
    if not ps:
        await update.effective_message.reply_text("فعلاً محصول فعالی برای فروش ثبت نشده است.",reply_markup=MENU); return
    await update.effective_message.reply_text("محصول موردنظر را انتخاب کنید:",reply_markup=product_keyboard(ps))

async def buy_selected(update,context):
    q=update.callback_query; await q.answer()
    if not q.message or q.message.chat.type!=ChatType.PRIVATE: return
    if store.get_verification_status(q.from_user.id)!="approved":
        await q.message.reply_text("دسترسی خرید شما تأیید نشده است.",reply_markup=MENU); return
    pid=int(q.data.split(":")[1])
    p=store.get_product(pid)
    if not p or not p["active"]:
        await q.message.reply_text("این محصول دیگر فعال نیست.",reply_markup=MENU); return
    context.user_data["buy_product"]=pid
    await q.message.reply_text(
        f"محصول: {p['name']}\nتوضیحات: {p['details']}\n"
        f"قیمت واحد: {p['price']} ریال\nموجودی: {p['stock']}\n\n"
        "مقدار موردنظر را به عدد وارد کنید:",
        reply_markup=ReplyKeyboardMarkup([[CANCEL]],resize_keyboard=True))

async def orders_cmd(update,context):
    if not await private(update): return
    rows=store.list_user_orders(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("هنوز سفارشی ندارید.",reply_markup=MENU); return
    lines=["📦 سفارش‌های شما:"]
    for o in rows:
        lines.append(f"{o['order_number']} | {o['product_name']} | {o['total_price']} {o['currency']} | {o['status']}")
    await update.effective_message.reply_text("\n".join(lines),reply_markup=MENU)

async def kyc_start(update,context):
    if not await private(update): return
    u=update.effective_user; store.remember_user(u.id,u.username,u.first_name)
    st=store.get_verification_status(u.id)
    if st=="approved":
        await update.effective_message.reply_text("احراز هویت شما قبلاً تأیید شده است.",reply_markup=MENU); return
    sid=store.request_kyc(u.id,u.username,u.first_name)
    context.user_data["kyc_id"]=sid; context.user_data["kyc_stage"]="phone"
    await update.effective_message.reply_text(
        "برای احراز هویت، ابتدا شماره موبایل خود را ارسال کنید.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 ارسال شماره موبایل", request_contact=True)],
             [CANCEL]], resize_keyboard=True)
    )

async def kyc_contact(update,context):
    if not await private(update): return
    c=update.effective_message.contact
    store.set_phone(update.effective_user.id,c.phone_number)
    sid=context.user_data.get("kyc_id") or store.request_kyc(update.effective_user.id,update.effective_user.username,update.effective_user.first_name)
    context.user_data["kyc_id"]=sid; context.user_data["kyc_stage"]="doc"
    await update.effective_message.reply_text(
        "شماره ثبت شد.\nحالا عکس مدرک شناسایی را ارسال کنید.\n"
        "بعد از ارسال، اگر مدرک دیگری لازم بود مدیر با شما هماهنگ می‌کند.",
        reply_markup=ReplyKeyboardMarkup([[CANCEL]],resize_keyboard=True))

async def kyc_text(update,context):
    if not await private(update): return False
    u=update.effective_user; text=(update.effective_message.text or "").strip()
    if text==CANCEL:
        context.user_data.pop("kyc_id",None); context.user_data.pop("kyc_stage",None)
        store.set_support_mode(u.id,False)
        await update.effective_message.reply_text("لغو شد.",reply_markup=MENU); return True
    stage=context.user_data.get("kyc_stage")
    if stage=="phone":
        store.set_phone(u.id,text)
        sid=context.user_data.get("kyc_id") or store.request_kyc(u.id,u.username,u.first_name)
        context.user_data["kyc_id"]=sid; context.user_data["kyc_stage"]="doc"
        await update.effective_message.reply_text("شماره ثبت شد. حالا عکس مدرک شناسایی را ارسال کنید.",reply_markup=ReplyKeyboardMarkup([[CANCEL]],resize_keyboard=True))
        return True
    return False

async def kyc_file(update,context):
    if not await private(update): return
    sid=context.user_data.get("kyc_id")
    if not sid:
        await update.effective_message.reply_text("ابتدا گزینه «احراز هویت» را انتخاب کنید.",reply_markup=MENU); return
    m=update.effective_message
    if m.photo:
        fid=m.photo[-1].file_id; typ="photo"
    elif m.document:
        fid=m.document.file_id; typ="document"
    else: return
    store.add_kyc_file(sid,typ,fid,m.caption)
    context.user_data["kyc_stage"]="done"
    await update.effective_message.reply_text(
        "مدرک دریافت شد و برای مدیر ارسال می‌شود. لطفاً منتظر بررسی بمانید.",
        reply_markup=MENU)
    await notify_admin_kyc(context,sid)

async def notify_admin_kyc(context,sid):
    aid=admin_id()
    if not aid: return
    s=store.get_kyc_submission(sid)
    if not s: return
    text=(f"🪪 درخواست احراز هویت جدید\nشناسه درخواست: {sid}\n"
          f"کاربر: {s['user_id']}\nنام: {s['first_name'] or '-'}\n"
          f"یوزرنیم: @{s['username'] if s['username'] else '-'}\nشماره: {s['phone'] or '-'}")
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأیید",callback_data=f"kyc_ok:{sid}"),
        InlineKeyboardButton("❌ رد",callback_data=f"kyc_no:{sid}")]])
    await context.bot.send_message(aid,text,reply_markup=kb)
    for f in store.get_kyc_files(sid):
        try:
            if f["file_type"]=="photo": await context.bot.send_photo(aid,f["file_id"],caption=f"مدرک KYC کاربر {s['user_id']}")
            else: await context.bot.send_document(aid,f["file_id"],caption=f"مدرک KYC کاربر {s['user_id']}")
        except TelegramError: pass

async def kyc_review(update,context):
    q=update.callback_query
    if q.from_user.id!=admin_id(): await q.answer("دسترسی ندارید.",show_alert=True); return
    await q.answer()
    action,sid=q.data.split(":"); sid=int(sid)
    uid=store.review_kyc(sid,"approved" if action=="kyc_ok" else "rejected")
    if not uid:
        await q.message.reply_text("این درخواست قبلاً بررسی شده است."); return
    await q.message.reply_text("تأیید شد." if action=="kyc_ok" else "رد شد.")
    try:
        await context.bot.send_message(uid,
            "✅ احراز هویت شما تأیید شد و خرید برای شما فعال شد." if action=="kyc_ok"
            else "❌ احراز هویت شما رد شد. برای پیگیری با پشتیبانی تماس بگیرید.")
    except TelegramError: pass

async def sell_start(update,context):
    if not await private(update): return
    context.user_data["sell_stage"]="asset"
    await update.effective_message.reply_text("نام ارز موردنظر برای فروش را بنویسید:",reply_markup=ReplyKeyboardMarkup([[CANCEL]],resize_keyboard=True))

async def sell_text(update,context):
    if not await private(update): return False
    text=(update.effective_message.text or "").strip(); stage=context.user_data.get("sell_stage")
    if text==CANCEL:
        context.user_data.pop("sell_stage",None); await update.effective_message.reply_text("لغو شد.",reply_markup=MENU); return True
    if stage=="asset":
        context.user_data["sell_asset"]=text; context.user_data["sell_stage"]="amount"
        await update.effective_message.reply_text("مقدار ارز را وارد کنید:"); return True
    if stage=="amount":
        context.user_data["sell_amount"]=text; context.user_data["sell_stage"]="wallet"
        await update.effective_message.reply_text("آدرس کیف پول مقصد برای واریز را وارد کنید:"); return True
    if stage=="wallet":
        u=update.effective_user
        sid=store.create_sale_request(u.id,context.user_data["sell_asset"],context.user_data["sell_amount"],text,None)
        asset=context.user_data["sell_asset"]; amount=context.user_data["sell_amount"]
        context.user_data.pop("sell_stage",None)
        await update.effective_message.reply_text(f"درخواست فروش #{sid} ثبت شد.\nارز: {asset}\nمقدار: {amount}\nمدیر پس از بررسی با شما تماس می‌گیرد.",reply_markup=MENU)
        aid=admin_id()
        if aid:
            await context.bot.send_message(aid,f"🔴 درخواست فروش جدید\nشناسه: {sid}\nکاربر: {u.id}\nارز: {asset}\nمقدار: {amount}\nکیف پول: {text}\n\n/sale_approve {sid}\n/sale_reject {sid}")
        return True
    return False

async def support_start(update,context):
    if not await private(update): return
    store.set_support_mode(update.effective_user.id,True)
    await update.effective_message.reply_text("پیام خود را بفرستید؛ برای مدیر ارسال می‌کنم. برای خروج /start را بزنید.",reply_markup=ReplyKeyboardMarkup([[CANCEL]],resize_keyboard=True))

async def support_any(update,context):
    if not await private(update): return
    u=update.effective_user; m=update.effective_message
    if not store.support_mode(u.id): return
    if m.text==CANCEL:
        store.set_support_mode(u.id,False); await m.reply_text("پشتیبانی لغو شد.",reply_markup=MENU); return
    aid=admin_id()
    if not aid: await m.reply_text("پشتیبانی فعلاً تنظیم نشده است.",reply_markup=MENU); return
    try:
        await context.bot.send_message(aid,f"📞 پیام پشتیبانی از {u.first_name or '-'} | user_id={u.id} | @{u.username or '-'}")
        await context.bot.copy_message(aid,m.chat.id,m.message_id)
        store.log_support(u.id,m.message_id,"user_to_admin")
        await m.reply_text("پیام شما برای مدیر ارسال شد. پاسخ از طریق ربات برایتان فرستاده می‌شود.",reply_markup=MENU)
        store.set_support_mode(u.id,False)
    except TelegramError:
        await m.reply_text("ارسال پیام انجام نشد؛ لطفاً دوباره تلاش کنید.")

async def reply_cmd(update,context):
    if not await require_admin(update): return
    if len(context.args)<2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("روش استفاده: /reply شناسه_کاربر متن")
        return
    uid=int(context.args[0]); text=" ".join(context.args[1:])
    try:
        await context.bot.send_message(uid,f"📞 پاسخ پشتیبانی:\n{text}")
        await update.effective_message.reply_text("ارسال شد.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"ارسال نشد: {type(e).__name__}")

async def products_cmd(update,context):
    if not await require_admin(update): return
    ps=store.list_products()
    if not ps: await update.effective_message.reply_text("کاتالوگ خالی است."); return
    lines=["📋 محصولات:"]
    for p in ps:
        lines.append(f"{p['product_id']} | {p['name']} | {p['details']} | {p['price']} ریال | موجودی {p['stock']} | {'فعال' if p['active'] else 'غیرفعال'}")
    await update.effective_message.reply_text("\n".join(lines))

def pipe_args(args,n):
    text=" ".join(args); parts=[x.strip() for x in text.split("|")]
    if len(parts)!=n: raise ValueError()
    return parts

async def product_add_cmd(update,context):
    if not await require_admin(update): return
    try: name,details,price,stock=pipe_args(context.args,4); p=store.add_product(name,details,price,"IRR",stock)
    except Exception as e:
        await update.effective_message.reply_text("فرمت اشتباه است:\n/product_add نام | توضیحات | قیمت ریالی | موجودی"); return
    await update.effective_message.reply_text(f"محصول #{p['product_id']} اضافه شد.")

async def product_update_cmd(update,context):
    if not await require_admin(update): return
    try:
        pid,name,details,price,stock=pipe_args(context.args,5); p=store.update_product(int(pid),name,details,price,"IRR",stock)
    except Exception:
        await update.effective_message.reply_text("فرمت:\n/product_update شناسه | نام | توضیحات | قیمت ریالی | موجودی"); return
    await update.effective_message.reply_text("محصول به‌روزرسانی شد." if p else "محصول پیدا نشد.")

async def product_toggle_cmd(update,context,active):
    if not await require_admin(update): return
    if len(context.args)!=1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("شناسه محصول را وارد کنید."); return
    ok=store.set_product_active(int(context.args[0]),active)
    await update.effective_message.reply_text("انجام شد." if ok else "محصول پیدا نشد.")

async def pending_kyc_cmd(update,context):
    if not await require_admin(update): return
    rows=store.get_pending_kyc()
    if not rows: await update.effective_message.reply_text("درخواست KYC در انتظار نیست."); return
    await update.effective_message.reply_text("\n".join([f"#{r['id']} | user {r['user_id']} | @{r['username'] or '-'}" for r in rows]))

async def pending_orders_cmd(update,context):
    if not await require_admin(update): return
    rows=store.list_pending_orders()
    if not rows: await update.effective_message.reply_text("سفارش پرداخت‌نشده‌ای نیست."); return
    await update.effective_message.reply_text("\n".join([f"{r['order_number']} | user {r['user_id']} | {r['product_name']} | {r['total_price']} ریال" for r in rows]))

async def pay_cmd(update,context):
    if not await require_admin(update): return
    if not context.args: await update.effective_message.reply_text("/pay شماره_سفارش [شناسه_تراکنش]"); return
    try:
        o,changed=store.mark_order_paid(context.args[0],context.args[1] if len(context.args)>1 else None,"manual")
    except Exception as e:
        await update.effective_message.reply_text(f"پرداخت ثبت نشد: {type(e).__name__}"); return
    await update.effective_message.reply_text("پرداخت تأیید شد و موجودی کسر شد." if changed else "این سفارش قبلاً پرداخت شده بود.")
    try:
        await context.bot.send_message(o["user_id"],f"✅ پرداخت سفارش {o['order_number']} تأیید شد.\nمبلغ: {o['total_price']} ریال")
    except TelegramError: pass
    await notify_admin_paid(context,o,manual=True)

async def notify_admin_paid(context,o,manual=False):
    aid=admin_id()
    if not aid: return
    await context.bot.send_message(aid,
        f"💰 پرداخت موفق\nسفارش: {o['order_number']}\nکاربر: {o['user_id']}\n"
        f"محصول: {o['product_name']}\nمبلغ: {o['total_price']} {o['currency']}\n"
        f"نوع تأیید: {'دستی' if manual else 'درگاه'}")

async def fail_cmd(update,context):
    if not await require_admin(update): return
    if not context.args: await update.effective_message.reply_text("/fail شماره_سفارش"); return
    ok=store.mark_order_failed(context.args[0]); await update.effective_message.reply_text("سفارش ناموفق شد." if ok else "تغییری انجام نشد.")

async def pending_sales_cmd(update,context):
    if not await require_admin(update): return
    rows=store.list_pending_sales()
    if not rows: await update.effective_message.reply_text("درخواست فروش در انتظار نیست."); return
    await update.effective_message.reply_text("\n".join([f"#{r['id']} | user {r['user_id']} | {r['asset']} | {r['amount']} | wallet {r['wallet'] or '-'}" for r in rows]))

async def sale_status_cmd(update,context,status):
    if not await require_admin(update): return
    if len(context.args)!=1 or not context.args[0].isdigit(): await update.effective_message.reply_text("شناسه درخواست را وارد کنید."); return
    uid=store.update_sale_status(int(context.args[0]),status)
    await update.effective_message.reply_text("وضعیت تغییر کرد." if uid else "درخواست پیدا نشد.")
    if uid:
        try: await context.bot.send_message(uid,f"وضعیت درخواست فروش شما: {'تأیید شد' if status=='approved' else 'رد شد'}")
        except TelegramError: pass

async def menu_router(update,context):
    if not await private(update): return
    m=update.effective_message
    if not m or not m.text: return
    u=update.effective_user; store.remember_user(u.id,u.username,u.first_name)
    # Active mini workflows first.
    if await kyc_text(update,context): return
    if await sell_text(update,context): return
    if store.support_mode(u.id):
        await support_any(update,context); return
    t=m.text
    if t==BUY: await buy_menu(update,context)
    elif t==SELL: await sell_start(update,context)
    elif t==KYC: await kyc_start(update,context)
    elif t==SUPPORT: await support_start(update,context)
    elif t==ORDERS: await orders_cmd(update,context)
    else: await m.reply_text("لطفاً یکی از گزینه‌های منو را انتخاب کنید.",reply_markup=MENU)

async def contact_handler(update,context):
    if not await private(update): return
    u=update.effective_user
    if context.user_data.get("kyc_stage") in {"phone","doc"}:
        await kyc_contact(update,context)

async def file_handler(update,context):
    if not await private(update): return
    if context.user_data.get("kyc_id"): await kyc_file(update,context)
    elif store.support_mode(update.effective_user.id): await support_any(update,context)

async def error_handler(update,context):
    logger.error("handler error: %s",type(context.error).__name__)

def build_app(token):
    app=Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help_cmd))
    app.add_handler(CommandHandler("id",id_cmd))
    app.add_handler(CommandHandler("products",products_cmd))
    app.add_handler(CommandHandler("product_add",product_add_cmd))
    app.add_handler(CommandHandler("product_update",product_update_cmd))
    app.add_handler(CommandHandler("product_disable",lambda u,c: product_toggle_cmd(u,c,False)))
    app.add_handler(CommandHandler("product_enable",lambda u,c: product_toggle_cmd(u,c,True)))
    app.add_handler(CommandHandler("pending_kyc",pending_kyc_cmd))
    app.add_handler(CommandHandler("pending_orders",pending_orders_cmd))
    app.add_handler(CommandHandler("pay",pay_cmd))
    app.add_handler(CommandHandler("fail",fail_cmd))
    app.add_handler(CommandHandler("pending_sales",pending_sales_cmd))
    app.add_handler(CommandHandler("sale_approve",lambda u,c: sale_status_cmd(u,c,"approved")))
    app.add_handler(CommandHandler("sale_reject",lambda u,c: sale_status_cmd(u,c,"rejected")))
    app.add_handler(CommandHandler("reply",reply_cmd))
    app.add_handler(CallbackQueryHandler(buy_selected,pattern=r"^buy:\d+$"))
    app.add_handler(CallbackQueryHandler(kyc_review,pattern=r"^kyc_(?:ok|no):\d+$"))
    app.add_handler(MessageHandler(filters.CONTACT,contact_handler))
    app.add_handler(MessageHandler(filters.PHOTO|filters.Document.ALL,file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,menu_router))
    app.add_error_handler(error_handler)
    return app

def main():
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    if not token: raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
    store.initialize_database()
    logger.info("Starting bot.")
    build_app(token).run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
