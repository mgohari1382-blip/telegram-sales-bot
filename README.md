# Persian Crypto Bot — Full version without payment gateway

This version includes:
- Persian main menu
- Manual KYC with phone + document/photo
- Admin KYC approve/reject
- Product catalog
- Product prices and inventory
- Buy flow and unique order number
- Inventory is deducted ONLY after confirmed payment
- Manual `/pay` command for testing
- Sell request flow
- Support forwarding to admin
- User order history
- Admin management commands

## Payment gateway
The real gateway is intentionally NOT connected yet.
Later we will add a gateway adapter that verifies the transaction server-side
and then calls:

`bot_storage.mark_order_paid(order_number, transaction_id, provider)`

Do not mark an order paid merely because the browser returned from a payment page.

## Render
Build command:
`pip install -r requirements.txt`

Start command:
`python main.py`

Environment variables:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_ADMIN_ID
- BOT_DB_PATH (optional; default `bot.sqlite3`)

## Admin commands
/products
/product_add نام | توضیحات | قیمت ریالی | موجودی
/product_update شناسه | نام | توضیحات | قیمت ریالی | موجودی
/product_disable شناسه
/product_enable شناسه
/pending_kyc
/pending_orders
/pay شماره_سفارش [شناسه_تراکنش]
/fail شماره_سفارش
/pending_sales
/sale_approve شناسه
/sale_reject شناسه
/reply شناسه_کاربر متن

Important: SQLite on a free ephemeral filesystem can be lost on a new instance/deploy. For a real production bot, attach persistent storage or move the database to a managed database.
