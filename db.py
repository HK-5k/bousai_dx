import os
import sqlite3
import unicodedata
from datetime import datetime

DB_PATH = os.environ.get("STOCK_DB_PATH", "stock.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def normalize_name(name):
    return unicodedata.normalize("NFKC", str(name)).strip()

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                name_norm TEXT,
                qty REAL DEFAULT 0,
                unit TEXT DEFAULT '',
                category TEXT,
                item_kind TEXT DEFAULT 'stock',
                subtype TEXT DEFAULT '',
                due_type TEXT DEFAULT 'none',
                due_date TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        # カラム追加用
        try:
            conn.execute("ALTER TABLE stocks ADD COLUMN item_kind TEXT DEFAULT 'stock'")
            conn.execute("ALTER TABLE stocks ADD COLUMN subtype TEXT DEFAULT ''")
            conn.execute("ALTER TABLE stocks ADD COLUMN unit TEXT DEFAULT ''")
        except:
            pass

def get_all_stocks():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM stocks").fetchall()]

def bulk_upsert(items, atomic=True):
    # 重複合算ロジック (v3仕様)
    with get_conn() as conn:
        for it in items:
            name = it.get('name')
            category = it.get('category')
            due_date = it.get('due_date')
            
            # 同じ品名・カテゴリ・期限があれば数量を加算、なければ新規作成
            existing = conn.execute(
                "SELECT id, qty FROM stocks WHERE name=? AND category=? AND due_date=?",
                (name, category, due_date)
            ).fetchone()
            
            if existing:
                new_qty = float(existing['qty']) + float(it.get('qty', 0))
                conn.execute("UPDATE stocks SET qty=?, updated_at=? WHERE id=?", 
                             (new_qty, datetime.now().isoformat(), existing['id']))
            else:
                conn.execute("""
                    INSERT INTO stocks (name, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, it.get('qty'), it.get('unit'), category, it.get('item_kind','stock'), 
                      it.get('subtype',''), it.get('due_type'), it.get('due_date'), it.get('memo'), 
                      datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
    return {"inserted": len(items)}

def delete_stock(id):
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id=?", (id,))
        conn.commit()

def clear_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks")
        conn.commit()
    
def update_stock(id, **kwargs):
    pass 
    
def get_recent_names(cat, limit=10):
    return []