"""Full Persian crypto sales/trading Telegram bot.

Features:
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

Render deployment:
- Uses Telegram Webhook instead of polling
- Opens 0.0.0.0:$PORT
- Works with Render Web Service
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import bot_storage as store


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=getattr(
        logging,
        os.getenv("LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
)

logger = logging.getLogger("crypto_bot")


BOT_TOKEN_PATTERN = re.compile(
    r"\b(?:bot)?\d{6,}:[A-Za-z0-9_-]{20,}\b",
    re.I,
)


class Redact(logging.Filter):
    def filter(self, record):
        record.msg = BOT_TOKEN_PATTERN.sub(
            "[REDACTED]",
            record.getMessage(),
        )
        record.args = ()
        return True


for handler in logging.getLogger().handlers:
    handler.addFilter(Redact())


# =========================================================
# MENU
# =========================================================

BUY = "🟢 خرید ارز"
SELL = "🔴 فروش ارز"
KYC = "🪪 احراز هویت"
SUPPORT = "📞 پشتیبانی"
ORDERS = "📦 سفارش‌های من"
CANCEL = "❌ لغو"

MENU = ReplyKeyboardMarkup(
    [
        [BUY, SELL],
        [KYC, SUPPORT],
        [ORDERS],
    ],
    resize_keyboard=True,
)


# =========================================================
# HELPERS
# =========================================================

def admin_id():
    raw = os.getenv("TELEGRAM_ADMIN_ID", "").strip()

    if raw.isdigit():
        return int(raw)

    return None


async def private(update):
    chat = update.effective_chat

    if chat and chat.type == ChatType.PRIVATE:
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "لطفاً در گفت‌وگوی خصوصی ربات ادامه دهید."
        )

    return False


async def require_admin(update):
    if not await private(update):
        return False

    if update.effective_user.id != admin_id():
        await update.effective_message.reply_text(
            "این بخش فقط برای مدیر است."
        )
        return False

    return True


# =========================================================
# START / HELP / ID
# =========================================================

async def start(update, context):
    if not await private(update):
        return

    user = update.effective_user

    store.remember_user(
        user.id,
        user.username,
        user.first_name,
    )

    await update.effective_message.reply_text(
        "سلام 👋\n"
        "به ربات خرید و فروش ارز خوش آمدید.\n\n"
        "برای خرید، ابتدا احراز هویت شما باید توسط مدیر تأیید شود.",
        reply_markup=MENU,
    )


async def help_cmd(update, context):
    if not await private(update):
        return

    text = (
        "راهنما:\n"
        "🟢 خرید ارز: انتخاب محصول و ساخت سفارش\n"
        "🔴 فروش ارز: ثبت درخواست فروش\n"
        "🪪 احراز هویت: ارسال اطلاعات و مدارک برای بررسی مدیر\n"
        "📞 پشتیبانی: ارسال مستقیم پیام برای مدیر\n"
        "📦 سفارش‌های من: مشاهده سفارش‌های قبلی"
    )

    if update.effective_user.id == admin_id():
        text += (
            "\n\nدستورات مدیر:\n"
            "/products\n"
            "/product_add نام | توضیحات | قیمت ریالی | موجودی\n"
            "/product_update شناسه | نام | توضیحات | قیمت ریالی | موجودی\n"
            "/product_disable شناسه\n"
            "/product_enable شناسه\n"
            "/pending_kyc\n"
            "/pending_orders\n"
            "/pay شماره_سفارش [شناسه_تراکنش]\n"
            "/fail شماره_سفارش\n"
            "/pending_sales\n"
            "/sale_approve شناسه\n"
            "/sale_reject شناسه\n"
            "/reply شناسه_کاربر متن"
        )

    await update.effective_message.reply_text(
        text,
        reply_markup=MENU,
    )


async def id_cmd(update, context):
    if not await private(update):
        return

    await update.effective_message.reply_text(
        f"شناسه عددی تلگرام شما: {update.effective_user.id}",
        reply_markup=MENU,
    )


# =========================================================
# PRODUCTS / BUY
# =========================================================

def product_keyboard(rows):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{p['name']} | {p['price']} ریال | موجودی {p['stock']}",
                    callback_data=f"buy:{p['product_id']}",
                )
            ]
            for p in rows
        ]
    )


async def buy_menu(update, context):
    if not await private(update):
        return

    user = update.effective_user

    status = store.get_verification_status(user.id)

    if status != "approved":
        msg = {
            "pending": "درخواست احراز هویت شما هنوز در حال بررسی است.",
            "rejected": "احراز هویت شما رد شده است؛ برای پیگیری با مدیر تماس بگیرید.",
        }.get(
            status,
            "برای خرید ابتدا گزینه «احراز هویت» را انتخاب و تأیید مدیر را دریافت کنید.",
        )

        await update.effective_message.reply_text(
            msg,
            reply_markup=MENU,
        )
        return

    products = store.list_products(True)

    if not products:
        await update.effective_message.reply_text(
            "فعلاً محصول فعالی برای فروش ثبت نشده است.",
            reply_markup=MENU,
        )
        return

    await update.effective_message.reply_text(
        "محصول موردنظر را انتخاب کنید:",
        reply_markup=product_keyboard(products),
    )


async def buy_selected(update, context):
    query = update.callback_query

    await query.answer()

    if not query.message:
        return

    if query.message.chat.type != ChatType.PRIVATE:
        return

    if store.get_verification_status(query.from_user.id) != "approved":
        await query.message.reply_text(
            "دسترسی خرید شما تأیید نشده است.",
            reply_markup=MENU,
        )
        return

    product_id = int(query.data.split(":")[1])

    product = store.get_product(product_id)

    if not product or not product["active"]:
        await query.message.reply_text(
            "این محصول دیگر فعال نیست.",
            reply_markup=MENU,
        )
        return

    context.user_data["buy_product"] = product_id

    await query.message.reply_text(
        f"محصول: {product['name']}\n"
        f"توضیحات: {product['details']}\n"
        f"قیمت واحد: {product['price']} ریال\n"
        f"موجودی: {product['stock']}\n\n"
        "مقدار موردنظر را به عدد وارد کنید:",
        reply_markup=ReplyKeyboardMarkup(
            [[CANCEL]],
            resize_keyboard=True,
        ),
    )


async def orders_cmd(update, context):
    if not await private(update):
        return

    rows = store.list_user_orders(
        update.effective_user.id
    )

    if not rows:
        await update.effective_message.reply_text(
            "هنوز سفارشی ندارید.",
            reply_markup=MENU,
        )
        return

    lines = ["📦 سفارش‌های شما:"]

    for order in rows:
        lines.append(
            f"{order['order_number']} | "
            f"{order['product_name']} | "
            f"{order['total_price']} "
            f"{order['currency']} | "
            f"{order['status']}"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=MENU,
    )


# =========================================================
# KYC
# =========================================================

async def kyc_start(update, context):
    if not await private(update):
        return

    user = update.effective_user

    store.remember_user(
        user.id,
        user.username,
        user.first_name,
    )

    status = store.get_verification_status(user.id)

    if status == "approved":
        await update.effective_message.reply_text(
            "احراز هویت شما قبلاً تأیید شده است.",
            reply_markup=MENU,
        )
        return

    submission_id = store.request_kyc(
        user.id,
        user.username,
        user.first_name,
    )

    context.user_data["kyc_id"] = submission_id
    context.user_data["kyc_stage"] = "phone"

    await update.effective_message.reply_text(
        "برای احراز هویت، ابتدا شماره موبایل خود را ارسال کنید.",
        reply_markup=ReplyKeyboardMarkup(
            [
                [
                    KeyboardButton(
                        "📱 ارسال شماره موبایل",
                        request_contact=True,
                    )
                ],
                [CANCEL],
            ],
            resize_keyboard=True,
        ),
    )


async def kyc_contact(update, context):
    if not await private(update):
        return

    contact = update.effective_message.contact

    store.set_phone(
        update.effective_user.id,
        contact.phone_number,
    )

    submission_id = (
        context.user_data.get("kyc_id")
        or store.request_kyc(
            update.effective_user.id,
            update.effective_user.username,
            update.effective_user.first_name,
        )
    )

    context.user_data["kyc_id"] = submission_id
    context.user_data["kyc_stage"] = "doc"

    await update.effective_message.reply_text(
        "شماره ثبت شد.\n"
        "حالا عکس مدرک شناسایی را ارسال کنید.\n"
        "بعد از ارسال، اگر مدرک دیگری لازم بود مدیر با شما هماهنگ می‌کند.",
        reply_markup=ReplyKeyboardMarkup(
            [[CANCEL]],
            resize_keyboard=True,
        ),
    )


async def kyc_text(update, context):
    if not await private(update):
        return False

    user = update.effective_user

    text = (
        update.effective_message.text or ""
    ).strip()

    if text == CANCEL:
        context.user_data.pop("kyc_id", None)
        context.user_data.pop("kyc_stage", None)

        store.set_support_mode(
            user.id,
            False,
        )

        await update.effective_message.reply_text(
            "لغو شد.",
            reply_markup=MENU,
        )

        return True

    stage = context.user_data.get("kyc_stage")

    if stage == "phone":
        store.set_phone(
            user.id,
            text,
        )

        submission_id = (
            context.user_data.get("kyc_id")
            or store.request_kyc(
                user.id,
                user.username,
                user.first_name,
            )
        )

        context.user_data["kyc_id"] = submission_id
        context.user_data["kyc_stage"] = "doc"

        await update.effective_message.reply_text(
            "شماره ثبت شد.\n"
            "حالا عکس مدرک شناسایی را ارسال کنید.",
            reply_markup=ReplyKeyboardMarkup(
                [[CANCEL]],
                resize_keyboard=True,
            ),
        )

        return True

    return False


async def kyc_file(update, context):
    if not await private(update):
        return

    submission_id = context.user_data.get("kyc_id")

    if not submission_id:
        await update.effective_message.reply_text(
            "ابتدا گزینه «احراز هویت» را انتخاب کنید.",
            reply_markup=MENU,
        )
        return

    message = update.effective_message

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"

    elif message.document:
        file_id = message.document.file_id
        file_type = "document"

    else:
        return

    store.add_kyc_file(
        submission_id,
        file_type,
        file_id,
        message.caption,
    )

    context.user_data["kyc_stage"] = "done"

    await update.effective_message.reply_text(
        "مدرک دریافت شد و برای مدیر ارسال می‌شود. "
        "لطفاً منتظر بررسی بمانید.",
        reply_markup=MENU,
    )

    await notify_admin_kyc(
        context,
        submission_id,
    )


async def notify_admin_kyc(context, submission_id):
    admin = admin_id()

    if not admin:
        return

    submission = store.get_kyc_submission(
        submission_id
    )

    if not submission:
        return

    text = (
        f"🪪 درخواست احراز هویت جدید\n"
        f"شناسه درخواست: {submission_id}\n"
        f"کاربر: {submission['user_id']}\n"
        f"نام: {submission['first_name'] or '-'}\n"
        f"یوزرنیم: @{submission['username'] if submission['username'] else '-'}\n"
        f"شماره: {submission['phone'] or '-'}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تأیید",
                    callback_data=f"kyc_ok:{submission_id}",
                ),
                InlineKeyboardButton(
                    "❌ رد",
                    callback_data=f"kyc_no:{submission_id}",
                ),
            ]
        ]
    )

    await context.bot.send_message(
        admin,
        text,
        reply_markup=keyboard,
    )

    for file in store.get_kyc_files(
        submission_id
    ):
        try:
            if file["file_type"] == "photo":
                await context.bot.send_photo(
                    admin,
                    file["file_id"],
                    caption=(
                        f"مدرک KYC کاربر "
                        f"{submission['user_id']}"
                    ),
                )
            else:
                await context.bot.send_document(
                    admin,
                    file["file_id"],
                    caption=(
                        f"مدرک KYC کاربر "
                        f"{submission['user_id']}"
                    ),
                )

        except TelegramError:
            pass


async def kyc_review(update, context):
    query = update.callback_query

    if query.from_user.id != admin_id():
        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )
        return

    await query.answer()

    action, submission_id = query.data.split(":")
    submission_id = int(submission_id)

    user_id = store.review_kyc(
        submission_id,
        "approved"
        if action == "kyc_ok"
        else "rejected",
    )

    if not user_id:
        await query.message.reply_text(
            "این درخواست قبلاً بررسی شده است."
        )
        return

    await query.message.reply_text(
        "تأیید شد."
        if action == "kyc_ok"
        else "رد شد."
    )

    try:
        await context.bot.send_message(
            user_id,
            (
                "✅ احراز هویت شما تأیید شد و خرید برای شما فعال شد."
                if action == "kyc_ok"
                else
                "❌ احراز هویت شما رد شد. "
                "برای پیگیری با پشتیبانی تماس بگیرید."
            ),
        )

    except TelegramError:
        pass


# =========================================================
# SELL
# =========================================================

async def sell_start(update, context):
    if not await private(update):
        return

    context.user_data["sell_stage"] = "asset"

    await update.effective_message.reply_text(
        "نام ارز موردنظر برای فروش را بنویسید:",
        reply_markup=ReplyKeyboardMarkup(
            [[CANCEL]],
            resize_keyboard=True,
        ),
    )


async def sell_text(update, context):
    if not await private(update):
        return False

    text = (
        update.effective_message.text or ""
    ).strip()

    stage = context.user_data.get(
        "sell_stage"
    )

    if text == CANCEL:
        context.user_data.pop(
            "sell_stage",
            None,
        )

        await update.effective_message.reply_text(
            "لغو شد.",
            reply_markup=MENU,
        )

        return True

    if stage == "asset":
        context.user_data["sell_asset"] = text
        context.user_data["sell_stage"] = "amount"

        await update.effective_message.reply_text(
            "مقدار ارز را وارد کنید:"
        )

        return True

    if stage == "amount":
        context.user_data["sell_amount"] = text
        context.user_data["sell_stage"] = "wallet"

        await update.effective_message.reply_text(
            "آدرس کیف پول مقصد برای واریز را وارد کنید:"
        )

        return True

    if stage == "wallet":
        user = update.effective_user

        submission_id = store.create_sale_request(
            user.id,
            context.user_data["sell_asset"],
            context.user_data["sell_amount"],
            text,
            None,
        )

        asset = context.user_data["sell_asset"]
        amount = context.user_data["sell_amount"]

        context.user_data.pop(
            "sell_stage",
            None,
        )

        await update.effective_message.reply_text(
            f"درخواست فروش #{submission_id} ثبت شد.\n"
            f"ارز: {asset}\n"
            f"مقدار: {amount}\n"
            f"مدیر پس از بررسی با شما تماس می‌گیرد.",
            reply_markup=MENU,
        )

        admin = admin_id()

        if admin:
            await context.bot.send_message(
                admin,
                f"🔴 درخواست فروش جدید\n"
                f"شناسه: {submission_id}\n"
                f"کاربر: {user.id}\n"
                f"ارز: {asset}\n"
                f"مقدار: {amount}\n"
                f"کیف پول: {text}\n\n"
                f"/sale_approve {submission_id}\n"
                f"/sale_reject {submission_id}",
            )

        return True

    return False


# =========================================================
# SUPPORT
# =========================================================

async def support_start(update, context):
    if not await private(update):
        return

    store.set_support_mode(
        update.effective_user.id,
        True,
    )

    await update.effective_message.reply_text(
        "پیام خود را بفرستید؛ برای مدیر ارسال می‌کنم.\n"
        "برای خروج /start را بزنید.",
        reply_markup=ReplyKeyboardMarkup(
            [[CANCEL]],
            resize_keyboard=True,
        ),
    )


async def support_any(update, context):
    if not await private(update):
        return

    user = update.effective_user
    message = update.effective_message

    if not store.support_mode(user.id):
        return

    if message.text == CANCEL:
        store.set_support_mode(
            user.id,
            False,
        )

        await message.reply_text(
            "پشتیبانی لغو شد.",
            reply_markup=MENU,
        )
        return

    admin = admin_id()

    if not admin:
        await message.reply_text(
            "پشتیبانی فعلاً تنظیم نشده است.",
            reply_markup=MENU,
        )
        return

    try:
        await context.bot.send_message(
            admin,
            f"📞 پیام پشتیبانی از "
            f"{user.first_name or '-'} | "
            f"user_id={user.id} | "
            f"@{user.username or '-'}",
        )

        await context.bot.copy_message(
            admin,
            message.chat.id,
            message.message_id,
        )

        store.log_support(
            user.id,
            message.message_id,
            "user
