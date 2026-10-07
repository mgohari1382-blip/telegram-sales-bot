import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent / "bot_data.sqlite3"

class PurchaseNotApproved(Exception): pass
class ProductUnavailable(Exception): pass
class OrderNotFound(Exception): pass
class PaymentStateConflict(Exception): pass
class DuplicateProduct(Exception): pass

@contextmanager
def connection():
    con = sqlite3.connect(DATABASE_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def now():
    return datetime.now(timezone.utc).isoformat()

def initialize_database():
    with connection() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            updated_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS products(
            product_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            details TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name COLLATE NOCASE, details COLLATE NOCASE))""")
        con.execute("""CREATE TABLE IF NOT EXISTS orders(
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, product_name TEXT NOT NULL,
            product_details TEXT NOT NULL, price TEXT NOT NULL,
            currency TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_payment',
            payment_provider TEXT, payment_transaction_id TEXT,
            payment_authority TEXT UNIQUE, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, admin_notified_at TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS orders_user_created ON orders(user_id,created_at DESC)")

def remember_user(user_id, username, first_name):
    with connection() as con:
        con.execute("""INSERT INTO users(user_id,username,first_name,updated_at)
        VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name,
        updated_at=excluded.updated_at""",(user_id,username,first_name,now()))

def get_verification_status(user_id):
    with connection() as con:
        row=con.execute("SELECT verification_status FROM users WHERE user_id=?",(user_id,)).fetchone()
    return row["verification_status"] if row else "unverified"

def request_verification(user_id, username, first_name):
    remember_user(user_id,username,first_name)
    if get_verification_status(user_id)=="approved": return "approved"
    with connection() as con:
        con.execute("UPDATE users SET verification_status='pending',updated_at=? WHERE user_id=?",(now(),user_id))
    return "pending"

def set_verification_status(user_id,status):
    if status not in {"unverified","pending","approved","rejected"}: raise ValueError("bad status")
    with connection() as con:
        cur=con.execute("UPDATE users SET verification_status=?,updated_at=? WHERE user_id=?",(status,now(),user_id))
    return cur.rowcount>0

def list_pending_users():
    with connection() as con:
        rows=con.execute("""SELECT user_id,username,first_name,updated_at FROM users
        WHERE verification_status='pending' ORDER BY updated_at ASC LIMIT 100""").fetchall()
    return [dict(x) for x in rows]

def add_product(name,details,price,currency):
    name,details,currency=name.strip(),details.strip(),currency.strip().upper()
    amount=Decimal(str(price))
    if not name or not details or amount<=0 or not amount.is_finite(): raise ValueError("مقادیر محصول نامعتبر است.")
    with connection() as con:
        try:
            cur=con.execute("""INSERT INTO products(name,details,price,currency,created_at,updated_at)
            VALUES(?,?,?,?,?,?)""",(name,details,format(amount.normalize(),"f"),currency,now(),now()))
        except sqlite3.IntegrityError as e: raise DuplicateProduct from e
        return dict(con.execute("SELECT * FROM products WHERE product_id=?",(cur.lastrowid,)).fetchone())

def update_product(pid,name,details,price,currency):
    amount=Decimal(str(price))
    with connection() as con:
        try:
            cur=con.execute("""UPDATE products SET name=?,details=?,price=?,currency=?,updated_at=?
            WHERE product_id=?""",(name.strip(),details.strip(),format(amount.normalize(),"f"),currency.strip().upper(),now(),pid))
        except sqlite3.IntegrityError as e: raise DuplicateProduct from e
    return cur.rowcount>0

def set_product_active(pid,active):
    with connection() as con:
        cur=con.execute("UPDATE products SET active=?,updated_at=? WHERE product_id=?",(int(active),now(),pid))
    return cur.rowcount>0

def list_products(active_only=False):
    q="SELECT * FROM products"+(" WHERE active=1" if active_only else "")+" ORDER BY product_id"
    with connection() as con: rows=con.execute(q).fetchall()
    return [dict(x) for x in rows]

def get_order(order_number):
    with connection() as con:
        row=con.execute("SELECT * FROM orders WHERE order_number=?",(order_number,)).fetchone()
    return dict(row) if row else None

def create_order(product_id,user_id):
    with connection() as con:
        user=con.execute("SELECT verification_status FROM users WHERE user_id=?",(user_id,)).fetchone()
        if not user or user["verification_status"]!="approved": raise PurchaseNotApproved
        product=con.execute("SELECT * FROM products WHERE product_id=? AND active=1",(product_id,)).fetchone()
        if not product: raise ProductUnavailable
        cur=con.execute("""INSERT INTO orders(user_id,product_id,product_name,product_details,price,currency,status,created_at,updated_at,order_number)
        VALUES(?,?,?,?,?,'pending_payment',?,?,?,?)""",
        (user_id,product_id,product["name"],product["details"],product["price"],product["currency"],now(),now(),"TEMP"))
        oid=cur.lastrowid
        number=f"ORD-{oid:08d}"
        con.execute("UPDATE orders SET order_number=? WHERE order_id=?",(number,oid))
        return dict(con.execute("SELECT * FROM orders WHERE order_id=?",(oid,)).fetchone())

def attach_authority(order_number,authority):
    with connection() as con:
        cur=con.execute("""UPDATE orders SET payment_authority=?,updated_at=?
        WHERE order_number=? AND status='pending_payment'""",(authority,now(),order_number))
    return cur.rowcount>0

def get_order_by_authority(authority):
    with connection() as con:
        row=con.execute("SELECT * FROM orders WHERE payment_authority=?",(authority,)).fetchone()
    return dict(row) if row else None

def apply_payment(order_number,provider,transaction_id,outcome):
    if outcome not in {"paid","failed"}: raise ValueError("bad outcome")
    with connection() as con:
        prior=con.execute("""SELECT * FROM orders WHERE payment_provider=? AND payment_transaction_id=?""",(provider,transaction_id)).fetchone()
        if prior:
            if prior["order_number"]==order_number and prior["status"]==outcome: return dict(prior),False
            raise PaymentStateConflict
        order=con.execute("SELECT * FROM orders WHERE order_number=?",(order_number,)).fetchone()
        if not order: raise OrderNotFound
        if order["status"]!="pending_payment": raise PaymentStateConflict
        con.execute("""UPDATE orders SET status=?,payment_provider=?,payment_transaction_id=?,updated_at=?
        WHERE order_id=?""",(outcome,provider,transaction_id,now(),order["order_id"]))
        return dict(con.execute("SELECT * FROM orders WHERE order_id=?",(order["order_id"],)).fetchone()),True

def mark_order_admin_notified(order_number):
    with connection() as con:
        cur=con.execute("""UPDATE orders SET admin_notified_at=? WHERE order_number=?
        AND status='paid' AND admin_notified_at IS NULL""",(now(),order_number))
    return cur.rowcount>0
