"""
香川防災DX - 備蓄品データ永続化（SQLite）
"""
import sqlite3
import os
from datetime import datetime
from pathlib import Path

# データディレクトリ（本番ではプロジェクト直下の data/）
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "stock.db"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn():
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # カラム名でアクセス可能に
    return conn


def init_db():
    """テーブルがなければ作成"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                qty TEXT NOT NULL,
                category TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def insert_stock(item: str, qty: str, category: str = "", memo: str = "") -> int:
    """備蓄品を1件登録。登録された id を返す"""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO stocks (item, qty, category, memo, created_at) VALUES (?, ?, ?, ?, ?)",
            (item, qty, category or "", memo or "", datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_all_stocks() -> list[dict]:
    """登録済み備蓄品を全件取得（新しい順）"""
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, item, qty, category, memo, created_at FROM stocks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_stock(stock_id: int) -> bool:
    """指定IDの備蓄品を削除"""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
