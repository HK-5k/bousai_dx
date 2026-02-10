import os
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Dict, List

DB_PATH = os.environ.get("STOCK_DB_PATH", "stock.db")


def get_conn() -> sqlite3.Connection:
    # timeout を入れてロック時の事故も減らす
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_name(name: Any) -> str:
    return unicodedata.normalize("NFKC", str(name or "")).strip()


def _has_column(conn: sqlite3.Connection, col: str) -> bool:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    return col in cols


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
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
            """
        )

        # 既存DBが古い場合に備え、無い列だけ追加
        add_cols = [
            ("name_norm", "ALTER TABLE stocks ADD COLUMN name_norm TEXT"),
            ("qty", "ALTER TABLE stocks ADD COLUMN qty REAL DEFAULT 0"),
            ("unit", "ALTER TABLE stocks ADD COLUMN unit TEXT DEFAULT ''"),
            ("category", "ALTER TABLE stocks ADD COLUMN category TEXT"),
            ("item_kind", "ALTER TABLE stocks ADD COLUMN item_kind TEXT DEFAULT 'stock'"),
            ("subtype", "ALTER TABLE stocks ADD COLUMN subtype TEXT DEFAULT ''"),
            ("due_type", "ALTER TABLE stocks ADD COLUMN due_type TEXT DEFAULT 'none'"),
            ("due_date", "ALTER TABLE stocks ADD COLUMN due_date TEXT DEFAULT ''"),
            ("memo", "ALTER TABLE stocks ADD COLUMN memo TEXT DEFAULT ''"),
            ("created_at", "ALTER TABLE stocks ADD COLUMN created_at TEXT"),
            ("updated_at", "ALTER TABLE stocks ADD COLUMN updated_at TEXT"),
        ]
        for col, ddl in add_cols:
            if not _has_column(conn, col):
                conn.execute(ddl)

        # NULL を正規化（古いDBのNULL混在で upsert がズレるのを防ぐ）
        conn.execute("UPDATE stocks SET unit='' WHERE unit IS NULL")
        conn.execute("UPDATE stocks SET subtype='' WHERE subtype IS NULL")
        conn.execute("UPDATE stocks SET due_type='none' WHERE due_type IS NULL OR due_type=''")
        conn.execute("UPDATE stocks SET due_date='' WHERE due_date IS NULL")
        conn.execute("UPDATE stocks SET item_kind='stock' WHERE item_kind IS NULL OR item_kind=''")
        conn.execute("UPDATE stocks SET memo='' WHERE memo IS NULL")
        conn.execute("UPDATE stocks SET created_at=? WHERE created_at IS NULL OR created_at=''", (_now(),))
        conn.execute("UPDATE stocks SET updated_at=? WHERE updated_at IS NULL OR updated_at=''", (_now(),))

        # name_norm を埋める
        rows = conn.execute(
            "SELECT id, name FROM stocks WHERE name_norm IS NULL OR name_norm=''"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE stocks SET name_norm=? WHERE id=?",
                (normalize_name(r["name"]), r["id"]),
            )

        # 検索を速くする（非ユニーク）
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_lookup
            ON stocks(name_norm, category, item_kind, due_type, due_date, unit, subtype)
            """
        )

        conn.commit()


def get_all_stocks() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute("SELECT * FROM stocks ORDER BY updated_at DESC, id DESC").fetchall()
        ]


def bulk_upsert(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    まず UPDATE（同一キーなら qty 加算）→ できなければ INSERT。
    INSERT が UNIQUE 等で落ちたら、旧キー(name+category+due_date) で UPDATE にフォールバック。
    """
    inserted = 0
    updated = 0
    now = _now()

    with get_conn() as conn:
        # トランザクション
        for it in items or []:
            name = normalize_name(it.get("name"))
            if not name:
                continue

            name_norm = normalize_name(name)
            category = str(it.get("category") or "").strip()
            unit = str(it.get("unit") or "").strip()
            item_kind = str(it.get("item_kind") or "stock").strip().lower() or "stock"
            subtype = str(it.get("subtype") or "").strip()
            due_type = str(it.get("due_type") or "none").strip().lower() or "none"
            due_date = str(it.get("due_date") or "").strip()
            memo = str(it.get("memo") or "").strip()

            try:
                qty = float(it.get("qty", 0) or 0)
            except Exception:
                qty = 0.0

            # 1) まず「新キー」で UPDATE（qty を加算）
            cur = conn.execute(
                """
                UPDATE stocks
                SET
                    name=?,
                    qty=COALESCE(qty,0) + ?,
                    unit=?,
                    category=?,
                    item_kind=?,
                    subtype=?,
                    due_type=?,
                    due_date=?,
                    memo=?,
                    updated_at=?
                WHERE
                    name_norm=?
                    AND category=?
                    AND COALESCE(item_kind,'stock')=?
                    AND COALESCE(due_type,'none')=?
                    AND COALESCE(due_date,'')=?
                    AND COALESCE(unit,'')=?
                    AND COALESCE(subtype,'')=?
                """,
                (
                    name, qty, unit, category, item_kind, subtype, due_type, due_date, memo, now,
                    name_norm, category, item_kind, due_type, due_date, unit, subtype
                ),
            )
            if cur.rowcount and cur.rowcount > 0:
                updated += 1
                continue

            # 2) INSERT してみる
            try:
                conn.execute(
                    """
                    INSERT INTO stocks
                    (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, now, now
                    ),
                )
                inserted += 1
                continue

            except sqlite3.IntegrityError:
                # 3) 旧DBの UNIQUE(name,category,due_date) 制約などに当たった場合の救済
                cur2 = conn.execute(
                    """
                    UPDATE stocks
                    SET
                        qty=COALESCE(qty,0) + ?,
                        unit=COALESCE(unit,''),
                        memo=?,
                        updated_at=?
                    WHERE
                        name=?
                        AND category=?
                        AND COALESCE(due_date,'')=?
                    """,
                    (qty, memo, now, name, category, due_date),
                )
                if cur2.rowcount and cur2.rowcount > 0:
                    updated += 1
                    continue

                # それでもダメなら例外を上げる（ここまで来るのは別制約）
                raise

        conn.commit()

    return {"inserted": inserted, "updated": updated}


def delete_stock(stock_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id=?", (int(stock_id),))
        conn.commit()


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks")
        conn.commit()
