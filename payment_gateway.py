# Payment gateway intentionally left unconfigured.
# The future gateway should verify callbacks server-side and then call:
#
# from bot_storage import mark_order_paid
# mark_order_paid(order_number, transaction_id, provider)
#
# Never trust a client-side "payment successful" message.
