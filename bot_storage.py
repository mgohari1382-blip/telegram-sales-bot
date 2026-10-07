
"""SQLite storage for the Persian crypto trading bot.

Payment gateway is intentionally not implemented here. Orders are created in
pending_payment state and inventory is reserved only when an admin marks an
order as paid manually. When a real gateway is added later, its verified
callback should call mark_order_paid(order_number, transaction_id, provider).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("BOT_DB_PATH", "bot.sqlite3"))

class StorageError(Exception): pass
class ProductUnavailable(StorageError): pass
class PurchaseNotApproved(StorageError): pass
class DuplicateProduct(StorageError): pass
class OrderNotFound(StorageError): pass
class InvalidOrderState(StorageError): pass

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()

def initialize_database() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            phone TEXT,
            support_mode INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kyc_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS kyc_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            file_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            caption TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(submission_id) REFERENCES kyc_submissions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            details TEXT NOT NULL,
            price TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'IRR',
            stock TEXT NOT NULL DEFAULT '0',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            product_details TEXT NOT NULL,
            quantity TEXT NOT NULL DEFAULT '1',
            unit_price TEXT NOT NULL,
            total_price TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'IRR',
            status TEXT NOT NULL DEFAULT 'pending_payment',
            payment_provider TEXT,
            payment_transaction_id TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS sales_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset TEXT NOT NULL,
            amount TEXT NOT NULL,
            wallet TEXT,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            direction TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_kyc_status ON kyc_submissions(status);
        """)

def remember_user(user_id: int, username: str|None, first_name: str|None) -> None:
    t=now()
    with db() as c:
        c.execute("""
        INSERT INTO users(user_id,username,first_name,created_at,updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username, first_name=excluded.first_name, updated_at=excluded.updated_at
        """,(user_id,username,first_name,t,t))

def get_user(user_id:int):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def get_verification_status(user_id:int)->str:
    row=get_user(user_id)
    return row["verification_status"] if row else "unverified"

def purchase_allowed(status:str)->bool:
    return status=="approved"

def set_phone(user_id:int, phone:str)->None:
    remember_user(user_id,None,None)
    with db() as c:
        c.execute("UPDATE users SET phone=?,updated_at=? WHERE user_id=?", (phone,now(),user_id))

def request_kyc(user_id:int, username:str|None, first_name:str|None)->int:
    remember_user(user_id,username,first_name)
    with db() as c:
        existing=c.execute("SELECT id FROM kyc_submissions WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()
        if existing: return int(existing["id"])
        c.execute("UPDATE users SET verification_status='pending',updated_at=? WHERE user_id=?",(now(),user_id))
        cur=c.execute("INSERT INTO kyc_submissions(user_id,status,created_at) VALUES(?,?,?)",(user_id,"pending",now()))
        return int(cur.lastrowid)

def add_kyc_file(submission_id:int,file_type:str,file_id:str,caption:str|None=None)->None:
    with db() as c:
        c.execute("INSERT INTO kyc_files(submission_id,file_type,file_id,caption,created_at) VALUES(?,?,?,?,?)",
                  (submission_id,file_type,file_id,caption,now()))

def get_pending_kyc():
    with db() as c:
        return c.execute("""
        SELECT k.id,k.user_id,k.created_at,u.username,u.first_name,u.phone
        FROM kyc_submissions k JOIN users u ON u.user_id=k.user_id
        WHERE k.status='pending' ORDER BY k.id
        """).fetchall()

def get_kyc_submission(submission_id:int):
    with db() as c:
        return c.execute("""
        SELECT k.*,u.username,u.first_name,u.phone
        FROM kyc_submissions k JOIN users u ON u.user_id=k.user_id
        WHERE k.id=?
        """,(submission_id,)).fetchone()

def get_kyc_files(submission_id:int):
    with db() as c:
        return c.execute("SELECT * FROM kyc_files WHERE submission_id=? ORDER BY id",(submission_id,)).fetchall()

def review_kyc(submission_id:int,status:str)->int|None:
    if status not in {"approved","rejected"}: raise ValueError("invalid KYC status")
    with db() as c:
        row=c.execute("SELECT user_id FROM kyc_submissions WHERE id=? AND status='pending'",(submission_id,)).fetchone()
        if not row: return None
        uid=int(row["user_id"])
        c.execute("UPDATE kyc_submissions SET status=?,reviewed_at=? WHERE id=?",(status,now(),submission_id))
        c.execute("UPDATE users SET verification_status=?,updated_at=? WHERE user_id=?",(status,now(),uid))
        return uid

def list_products(active_only=False):
    with db() as c:
        q="SELECT * FROM products"
        if active_only: q+=" WHERE active=1"
        q+=" ORDER BY product_id"
        return c.execute(q).fetchall()

def get_product(product_id:int):
    with db() as c:
        return c.execute("SELECT * FROM products WHERE product_id=?",(product_id,)).fetchone()

def _money(v:str)->str:
    try:
        d=Decimal(str(v).replace(",","").strip())
    except InvalidOperation: raise ValueError("invalid amount")
    if d<=0: raise ValueError("amount must be positive")
    return format(d,"f")

def _stock(v:str)->str:
    try:
        d=Decimal(str(v).replace(",","").strip())
    except InvalidOperation: raise ValueError("invalid stock")
    if d<0: raise ValueError("stock must be nonnegative")
    return format(d,"f")

def add_product(name:str,details:str,price:str,currency:str,stock:str):
    name=name.strip(); details=details.strip(); currency=currency.strip().upper()
    if not name or not details: raise ValueError("name/details required")
    price=_money(price); stock=_stock(stock)
    if currency!="IRR": raise ValueError("payment products must use IRR")
    try:
        with db() as c:
            cur=c.execute("""INSERT INTO products(name,details,price,currency,stock,active,created_at,updated_at)
                            VALUES(?,?,?,?,?,1,?,?)""",(name,details,price,currency,stock,now(),now()))
            return get_product(int(cur.lastrowid))
    except sqlite3.IntegrityError as e:
        raise DuplicateProduct() from e

def update_product(product_id:int,name:str,details:str,price:str,currency:str,stock:str):
    price=_money(price); stock=_stock(stock); currency=currency.strip().upper()
    if currency!="IRR": raise ValueError("payment products must use IRR")
    with db() as c:
        try:
            cur=c.execute("""UPDATE products SET name=?,details=?,price=?,currency=?,stock=?,updated_at=?
                             WHERE product_id=?""",(name.strip(),details.strip(),price,currency,stock,now(),product_id))
        except sqlite3.IntegrityError as e: raise DuplicateProduct() from e
    return get_product(product_id) if cur.rowcount else None

def set_product_active(product_id:int,active:bool)->bool:
    with db() as c:
        cur=c.execute("UPDATE products SET active=?,updated_at=? WHERE product_id=?",(1 if active else 0,now(),product_id))
        return cur.rowcount>0

def create_order(product_id:int,user_id:int,quantity:str="1"):
    if not purchase_allowed(get_verification_status(user_id)): raise PurchaseNotApproved()
    try: qty=Decimal(str(quantity).replace(",",""))
    except InvalidOperation: raise ValueError("invalid quantity")
    if qty<=0: raise ValueError("quantity must be positive")
    with db() as c:
        p=c.execute("SELECT * FROM products WHERE product_id=? AND active=1",(product_id,)).fetchone()
        if not p: raise ProductUnavailable()
        stock=Decimal(p["stock"])
        if stock < qty: raise ProductUnavailable("موجودی کافی نیست.")
        total=(Decimal(p["price"])*qty)
        # Stock is NOT deducted here. It is deducted only on confirmed payment.
        t=now()
        cur=c.execute("""INSERT INTO orders(order_number,user_id,product_id,product_name,product_details,quantity,unit_price,total_price,currency,status,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,'pending_payment',?)""",
                      ("TEMP",user_id,product_id,p["name"],p["details"],format(qty,"f"),p["price"],format(total,"f"),p["currency"],t))
        oid=int(cur.lastrowid)
        order_no=f"ORD-{oid:08d}"
        c.execute("UPDATE orders SET order_number=? WHERE id=?",(order_no,oid))
    return get_order(order_no)

def get_order(order_number:str):
    with db() as c:
        return c.execute("SELECT * FROM orders WHERE order_number=?",(order_number,)).fetchone()

def list_user_orders(user_id:int,limit:int=10):
    with db() as c:
        return c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",(user_id,limit)).fetchall()

def list_pending_orders(limit:int=20):
    with db() as c:
        return c.execute("SELECT * FROM orders WHERE status='pending_payment' ORDER BY id LIMIT ?",(limit,)).fetchall()

def mark_order_paid(order_number:str,transaction_id:str|None=None,provider:str="manual"):
    with db() as c:
        o=c.execute("SELECT * FROM orders WHERE order_number=?",(order_number,)).fetchone()
        if not o: raise OrderNotFound()
        if o["status"]=="paid": return o,False
        if o["status"]!="pending_payment": raise InvalidOrderState()
        p=c.execute("SELECT stock FROM products WHERE product_id=?",(o["product_id"],)).fetchone()
        if not p: raise ProductUnavailable()
        stock=Decimal(p["stock"]); qty=Decimal(o["quantity"])
        if stock < qty: raise ProductUnavailable("موجودی برای پرداخت کافی نیست.")
        new_stock=format(stock-qty,"f")
        c.execute("UPDATE products SET stock=?,updated_at=? WHERE product_id=?",(new_stock,now(),o["product_id"]))
        c.execute("""UPDATE orders SET status='paid',payment_provider=?,payment_transaction_id=?,paid_at=? WHERE order_number=?""",
                  (provider,transaction_id,now(),order_number))
    return get_order(order_number),True

def mark_order_failed(order_number:str):
    with db() as c:
        cur=c.execute("UPDATE orders SET status='failed' WHERE order_number=? AND status='pending_payment'",(order_number,))
        return cur.rowcount>0

def create_sale_request(user_id:int,asset:str,amount:str,wallet:str|None,note:str|None):
    asset=asset.strip(); amount=amount.strip()
    if not asset or not amount: raise ValueError("asset/amount required")
    with db() as c:
        cur=c.execute("""INSERT INTO sales_requests(user_id,asset,amount,wallet,note,status,created_at,updated_at)
                         VALUES(?,?,?,?,?,'pending',?,?)""",(user_id,asset,amount,wallet,note,now(),now()))
        return int(cur.lastrowid)

def list_pending_sales(limit:int=30):
    with db() as c:
        return c.execute("SELECT * FROM sales_requests WHERE status='pending' ORDER BY id LIMIT ?",(limit,)).fetchall()

def update_sale_status(sale_id:int,status:str)->int|None:
    if status not in {"approved","rejected","paid"}: raise ValueError("invalid status")
    with db() as c:
        row=c.execute("SELECT user_id FROM sales_requests WHERE id=? AND status='pending'",(sale_id,)).fetchone()
        if not row: return None
        c.execute("UPDATE sales_requests SET status=?,updated_at=? WHERE id=?",(status,now(),sale_id))
        return int(row["user_id"])

def set_support_mode(user_id:int,enabled:bool):
    remember_user(user_id,None,None)
    with db() as c: c.execute("UPDATE users SET support_mode=?,updated_at=? WHERE user_id=?",(1 if enabled else 0,now(),user_id))

def support_mode(user_id:int)->bool:
    row=get_user(user_id); return bool(row and row["support_mode"])

def log_support(user_id:int,message_id:int,direction:str):
    with db() as c: c.execute("INSERT INTO support_messages(user_id,message_id,direction,created_at) VALUES(?,?,?,?)",(user_id,message_id,direction,now()))
