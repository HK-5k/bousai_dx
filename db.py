"""
香川防災DX - 備蓄品データ永続化（SQLite）
items（stocks）テーブル: マイグレーションで status, spec, maintenance_date, category を保証
"""
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "stock.db"

# カテゴリ候補（リスト表示・フォーム用）
CATEGORIES = [
    "主食類",
    "副食等",
    "水・飲料",
    "乳幼児用品",
    "衛生・トイレ",
    "寝具・避難環境",
    "資機材・重要設備",
]

# 状態候補（資機材・表示用）
STATUSES = ["稼働可", "修理中", "要点検", "期限切れ", "貸出中", "その他"]


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn():
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def init_db():
    """テーブル作成。不足カラムはマイグレーションで追加。"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                qty TEXT NOT NULL,
                memo TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

        # マイグレーション: なければ追加
        for col, typ in [
            ("category", "TEXT DEFAULT ''"),
            ("status", "TEXT DEFAULT '稼働可'"),
            ("spec", "TEXT DEFAULT ''"),
            ("maintenance_date", "TEXT DEFAULT ''"),
        ]:
            if not _column_exists(conn, "stocks", col):
                conn.execute(f"ALTER TABLE stocks ADD COLUMN {col} {typ}")
        conn.commit()
    finally:
        conn.close()


def insert_stock(
    item: str,
    qty: str,
    category: str = "",
    memo: str = "",
    status: str = "",
    spec: str = "",
    maintenance_date: str = "",
) -> int:
    """備蓄品を1件登録。"""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO stocks (item, qty, category, memo, created_at, status, spec, maintenance_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item,
                qty,
                category or "",
                memo or "",
                datetime.now().isoformat(),
                status or "稼働可",
                spec or "",
                maintenance_date or "",
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_all_stocks() -> list[dict]:
    """登録済みを全件取得（新しい順）。"""
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, item, qty, category, memo, created_at, status, spec, maintenance_date FROM stocks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def bulk_insert_from_rows(rows: list[dict]) -> int:
    """CSV等の辞書リストを一括登録。キー: item, qty, category, memo, status, spec, maintenance_date。"""
    init_db()
    conn = _get_conn()
    n = 0
    try:
        for r in rows:
            item = str(r.get("item") or r.get("品名") or r.get("name") or "").strip()
            if not item:
                continue
            qty = str(r.get("qty") or r.get("数量") or r.get("quantity") or "1").strip()
            category = str(r.get("category") or r.get("カテゴリ") or "").strip()
            memo = str(r.get("memo") or r.get("備考") or "").strip()
            status = str(r.get("status") or r.get("状態") or "稼働可").strip()
            spec = str(r.get("spec") or r.get("仕様") or "").strip()
            maintenance_date = str(r.get("maintenance_date") or r.get("最終点検日") or r.get("賞味期限") or "").strip()
            conn.execute(
                """INSERT INTO stocks (item, qty, category, memo, created_at, status, spec, maintenance_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (item, qty, category, memo, datetime.now().isoformat(), status, spec, maintenance_date),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n
