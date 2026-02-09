"""
香川防災DX - 備蓄品データ永続化（SQLite）
items（stocks）テーブル: マイグレーションで status, spec, maintenance_date, category, due_type を保証
"""
import re
import sqlite3
import unicodedata
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


def normalize_name(text: str) -> str:
    """品名の正規化: 全角半角統一（NFKC）、前後空白除去、連続空白を1つに。"""
    if not text or not isinstance(text, str):
        return ""
    s = unicodedata.normalize("NFKC", text.strip())
    return re.sub(r"\s+", " ", s).strip()


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
            ("due_type", "TEXT DEFAULT '賞味期限'"),
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
    due_type: str = "賞味期限",
) -> int:
    """備蓄品を1件登録。"""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO stocks (item, qty, category, memo, created_at, status, spec, maintenance_date, due_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item,
                qty,
                category or "",
                memo or "",
                datetime.now().isoformat(),
                status or "稼働可",
                spec or "",
                maintenance_date or "",
                due_type or "賞味期限",
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
            "SELECT id, item, qty, category, memo, created_at, status, spec, maintenance_date, due_type FROM stocks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_stock_by_name(name: str) -> dict | None:
    """品名で検索（正規化して完全一致）。最初の1件を返す。"""
    n = normalize_name(name)
    if not n:
        return None
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, item, qty, category, memo, created_at, status, spec, maintenance_date, due_type FROM stocks WHERE TRIM(item) = ? LIMIT 1",
            (n,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_stock_by_name_due(name: str, due_type: str, due_date: str) -> dict | None:
    """name + due_type + due_date が完全一致する1件を返す（合算用）。"""
    n = normalize_name(name)
    if not n:
        return None
    dt = (due_type or "").strip() or "賞味期限"
    dd = (due_date or "").strip()
    init_db()
    conn = _get_conn()
    try:
        # due_type がない古いレコードは maintenance_date のみで比較
        row = conn.execute(
            """SELECT id, item, qty, category, memo, created_at, status, spec, maintenance_date, due_type
               FROM stocks WHERE TRIM(item) = ? AND COALESCE(due_type, '賞味期限') = ? AND COALESCE(TRIM(maintenance_date), '') = ? LIMIT 1""",
            (n, dt, dd),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_stock(
    stock_id: int,
    *,
    item: str = None,
    qty: str = None,
    category: str = None,
    memo: str = None,
    status: str = None,
    spec: str = None,
    maintenance_date: str = None,
    due_type: str = None,
) -> bool:
    """指定IDの備蓄品を更新。指定したフィールドのみ更新。"""
    init_db()
    conn = _get_conn()
    try:
        updates = []
        vals = []
        for col, val in [
            ("item", item),
            ("qty", qty),
            ("category", category),
            ("memo", memo),
            ("status", status),
            ("spec", spec),
            ("maintenance_date", maintenance_date),
            ("due_type", due_type),
        ]:
            if val is not None:
                updates.append(f"{col} = ?")
                vals.append(val)
        if not updates:
            return False
        vals.append(stock_id)
        conn.execute(f"UPDATE stocks SET {', '.join(updates)} WHERE id = ?", vals)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_stock(stock_id: int) -> bool:
    """指定IDの備蓄品を削除。"""
    init_db()
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _add_qty_str(a: str, b: str) -> str | None:
    """数量文字列を数値として加算し文字列で返す。不可なら None。"""
    try:
        an = int(re.sub(r"[^0-9]", "", str(a)) or "0")
        bn = int(re.sub(r"[^0-9]", "", str(b)) or "0")
        return str(an + bn)
    except (ValueError, TypeError):
        return None


def bulk_register_with_merge(items: list[dict]) -> tuple[list[str], bool]:
    """
    カート一括登録。name + due_type + due_date が一致する既存は数量加算、否则は新規INSERT。
    トランザクションで実行し、失敗時はロールバック。戻り値: (ログメッセージ一覧, 成功したか)
    """
    init_db()
    conn = _get_conn()
    logs = []
    try:
        conn.execute("BEGIN")
        for p in items:
            name_raw = (p.get("name") or p.get("item") or "").strip()
            if not name_raw:
                continue
            name = normalize_name(name_raw)
            qty = (p.get("qty") or "1").strip()
            due_type = (p.get("due_type") or "賞味期限").strip() or "賞味期限"
            due_date = (p.get("due_date") or "").strip()
            category = (p.get("category") or "").strip()
            memo = (p.get("memo") or "").strip()
            status = (p.get("status") or "稼働可").strip()
            spec = (p.get("spec") or "").strip()

            row = conn.execute(
                """SELECT id, qty FROM stocks
                   WHERE TRIM(item) = ? AND COALESCE(due_type, '賞味期限') = ? AND COALESCE(TRIM(maintenance_date), '') = ? LIMIT 1""",
                (name, due_type, due_date),
            ).fetchone()

            if row:
                rid, existing_qty = row["id"], row["qty"]
                new_qty = _add_qty_str(existing_qty, qty)
                if new_qty is not None:
                    conn.execute("UPDATE stocks SET qty = ? WHERE id = ?", (new_qty, rid))
                    logs.append(f"{name}（既存）に +{qty} しました")
                else:
                    conn.execute(
                        """INSERT INTO stocks (item, qty, category, memo, created_at, status, spec, maintenance_date, due_type)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name, qty, category, memo, datetime.now().isoformat(), status, spec, due_date, due_type),
                    )
                    logs.append(f"{name}（新規・数量は別レコード）を登録しました")
            else:
                conn.execute(
                    """INSERT INTO stocks (item, qty, category, memo, created_at, status, spec, maintenance_date, due_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, qty, category, memo, datetime.now().isoformat(), status, spec, due_date, due_type),
                )
                logs.append(f"{name}（新規）を登録しました")
        conn.commit()
        return logs, True
    except Exception:
        conn.rollback()
        return logs, False
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
