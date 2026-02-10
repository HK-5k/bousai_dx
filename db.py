import os
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Dict, List

# 環境変数があれば優先
DB_PATH = os.environ.get("STOCK_DB_PATH", "stock.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_name(name: Any) -> str:
    return unicodedata.normalize("NFKC", str(name or "")).strip()


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

        # 既存DB移行（失敗しても無視）
        for ddl in [
            "ALTER TABLE stocks ADD COLUMN name_norm TEXT",
            "ALTER TABLE stocks ADD COLUMN unit TEXT DEFAULT ''",
            "ALTER TABLE stocks ADD COLUMN item_kind TEXT DEFAULT 'stock'",
            "ALTER TABLE stocks ADD COLUMN subtype TEXT DEFAULT ''",
            "ALTER TABLE stocks ADD COLUMN due_type TEXT DEFAULT 'none'",
            "ALTER TABLE stocks ADD COLUMN due_date TEXT DEFAULT ''",
            "ALTER TABLE stocks ADD COLUMN memo TEXT DEFAULT ''",
            "ALTER TABLE stocks ADD COLUMN created_at TEXT",
            "ALTER TABLE stocks ADD COLUMN updated_at TEXT",
        ]:
            try:
                conn.execute(ddl)
            except Exception:
                pass

        # name_norm を補完
        try:
            conn.execute("UPDATE stocks SET name_norm = name WHERE name_norm IS NULL OR name_norm = ''")
        except Exception:
            pass

        conn.commit()


def get_all_stocks() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM stocks ORDER BY updated_at DESC, id DESC").fetchall()]


def bulk_upsert(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    重複判定: name_norm + category + due_type + due_date が一致したら qty を加算
    """
    inserted = 0
    updated = 0

    with get_conn() as conn:
        for it in items or []:
            name = normalize_name(it.get("name", ""))
            if not name:
                continue

            name_norm = name
            category = str(it.get("category") or "").strip()
            due_type = str(it.get("due_type") or "none").strip().lower() or "none"
            due_date = str(it.get("due_date") or "").strip()

            try:
                qty = float(it.get("qty", 0) or 0)
            except Exception:
                qty = 0.0

            unit = str(it.get("unit") or "").strip()
            item_kind = str(it.get("item_kind") or "stock").strip().lower() or "stock"
            subtype = str(it.get("subtype") or "").strip()
            memo = str(it.get("memo") or "").strip()
            now = _now_iso()

            existing = conn.execute(
                """
                SELECT id, qty FROM stocks
                WHERE name_norm = ? AND category = ? AND due_type = ? AND due_date = ?
                """,
                (name_norm, category, due_type, due_date),
            ).fetchone()

            if existing:
                new_qty = float(existing["qty"] or 0) + qty
                conn.execute(
                    """
                    UPDATE stocks
                    SET name = ?, qty = ?, unit = ?, item_kind = ?, subtype = ?, memo = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, new_qty, unit, item_kind, subtype, memo, now, existing["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO stocks
                    (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, now, now),
                )
                inserted += 1

        conn.commit()

    return {"inserted": inserted, "updated": updated}


def delete_stock(stock_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id = ?", (int(stock_id),))
        conn.commit()


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks")
        conn.commit()
