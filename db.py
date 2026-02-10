import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Tuple

# =========================
# SQLite settings
# =========================
DB_PATH = os.environ.get("STOCK_DB_PATH", "stock.db")

# Increase this if you change schema or migration behavior.
SCHEMA_VERSION = 3

# Canonical category keys used by the app (normalize legacy strings into these)
CANON_CATEGORIES = [
    "水・飲料",
    "主食類",
    "トイレ・衛生",
    "乳幼児用品",
    "寝具・避難",
    "資機材",
    "その他",
]

VALID_DUE_TYPES = {"none", "expiry", "inspection"}
VALID_ITEM_KINDS = {"stock", "capacity"}

_DATE_RE = re.compile(r"(\d{4})\D(\d{1,2})\D(\d{1,2})")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _nfkc(s: Any) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def normalize_name(name: Any) -> str:
    return _nfkc(name)


def normalize_category(cat: Any) -> str:
    s = _nfkc(cat)
    for k in CANON_CATEGORIES:
        if k != "その他" and k in s:
            return k
    if "その他" in s:
        return "その他"
    return "その他"


def normalize_due_type(due_type: Any) -> str:
    t = _nfkc(due_type).lower()
    return t if t in VALID_DUE_TYPES else "none"


def normalize_item_kind(item_kind: Any) -> str:
    k = _nfkc(item_kind).lower()
    return k if k in VALID_ITEM_KINDS else "stock"


def normalize_due_date(d: Any) -> str:
    """Return YYYY-MM-DD or ''."""
    if d is None:
        return ""
    s = str(d).strip()
    if not s:
        return ""
    # Already ISO
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date().isoformat()
    except Exception:
        pass
    # Date only
    try:
        return datetime.fromisoformat(s.split("T")[0]).date().isoformat()
    except Exception:
        pass
    m = _DATE_RE.search(s)
    if not m:
        return ""
    y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, da).date().isoformat()
    except Exception:
        return ""


def get_conn() -> sqlite3.Connection:
    """
    WAL + busy_timeout を適用した接続を返す（best-effort）。
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row

    # WAL
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass

    # Busy timeout (ms)
    try:
        conn.execute("PRAGMA busy_timeout=30000;")  # 30s
    except Exception:
        pass

    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _get_user_version(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])
    except Exception:
        return 0


def _set_user_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute(f"PRAGMA user_version={int(v)};")


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols


def _has_unwanted_unique_index(conn: sqlite3.Connection) -> bool:
    """
    Legacy UNIQUE constraints/indexes can cause sqlite3.IntegrityError on insert.
    Detect them to trigger a safe rebuild.
    """
    try:
        rows = conn.execute("PRAGMA index_list(stocks);").fetchall()
    except Exception:
        return False

    for r in rows:
        name = r["name"]
        unique = int(r["unique"])
        origin = r["origin"] if "origin" in r.keys() else None

        if unique != 1:
            continue
        if origin == "pk":
            continue
        if name == "ux_stocks_key":
            continue
        return True
    return False


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            qty REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            item_kind TEXT NOT NULL DEFAULT 'stock',
            subtype TEXT NOT NULL DEFAULT '',
            due_type TEXT NOT NULL DEFAULT 'none',
            due_date TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_stocks_key
        ON stocks(name_norm, category, item_kind, due_type, due_date, unit, subtype)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stocks_category_updated
        ON stocks(category, updated_at DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stocks_due
        ON stocks(due_type, due_date)
        """
    )


def rebuild_db(conn: sqlite3.Connection) -> str:
    """
    Rebuild stocks table into the canonical schema and normalize legacy records.
    Data is preserved into a renamed legacy table.
    Returns legacy table name (or '').
    """
    if not _table_exists(conn, "stocks"):
        _create_schema(conn)
        _set_user_version(conn, SCHEMA_VERSION)
        conn.commit()
        return ""

    legacy = f"stocks_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn.execute(f"ALTER TABLE stocks RENAME TO {legacy};")
    _create_schema(conn)

    legacy_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({legacy});").fetchall()}

    def get(row: sqlite3.Row, key: str, default: Any = "") -> Any:
        return row[key] if key in legacy_cols else default

    rows = conn.execute(f"SELECT * FROM {legacy};").fetchall()
    now = _now()

    for r in rows:
        name = normalize_name(get(r, "name", ""))
        if not name:
            continue
        name_norm = normalize_name(get(r, "name_norm", "")) or normalize_name(name)

        try:
            qty = float(get(r, "qty", 0) or 0)
        except Exception:
            qty = 0.0

        unit = _nfkc(get(r, "unit", ""))
        category = normalize_category(get(r, "category", ""))
        item_kind = normalize_item_kind(get(r, "item_kind", "stock"))
        subtype = _nfkc(get(r, "subtype", ""))
        due_type = normalize_due_type(get(r, "due_type", "none"))
        due_date = normalize_due_date(get(r, "due_date", ""))
        memo = _nfkc(get(r, "memo", ""))

        created_at = str(get(r, "created_at", "")) or now
        updated_at = str(get(r, "updated_at", "")) or now

        conn.execute(
            """
            INSERT INTO stocks
            (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name_norm, category, item_kind, due_type, due_date, unit, subtype)
            DO UPDATE SET
                qty = stocks.qty + excluded.qty,
                memo = CASE
                    WHEN excluded.memo != '' THEN excluded.memo
                    ELSE stocks.memo
                END,
                updated_at = excluded.updated_at
            """,
            (
                name,
                name_norm,
                qty,
                unit,
                category,
                item_kind,
                subtype,
                due_type,
                due_date,
                memo,
                created_at,
                updated_at,
            ),
        )

    _set_user_version(conn, SCHEMA_VERSION)
    conn.commit()
    return legacy


def init_db() -> None:
    with get_conn() as conn:
        existed = _table_exists(conn, "stocks")

        _create_schema(conn)

        # Fresh DB: just set version (no legacy table creation)
        if not existed:
            _set_user_version(conn, SCHEMA_VERSION)
            conn.commit()
            return

        user_v = _get_user_version(conn)
        need_rebuild = user_v < SCHEMA_VERSION

        required_cols = [
            "name",
            "name_norm",
            "qty",
            "unit",
            "category",
            "item_kind",
            "subtype",
            "due_type",
            "due_date",
            "memo",
            "created_at",
            "updated_at",
        ]
        for c in required_cols:
            if not _has_column(conn, "stocks", c):
                need_rebuild = True
                break

        if _has_unwanted_unique_index(conn):
            need_rebuild = True

        if need_rebuild:
            rebuild_db(conn)
        else:
            # Light normalization for NULLs
            conn.execute("UPDATE stocks SET unit='' WHERE unit IS NULL;")
            conn.execute("UPDATE stocks SET subtype='' WHERE subtype IS NULL;")
            conn.execute("UPDATE stocks SET memo='' WHERE memo IS NULL;")
            conn.execute("UPDATE stocks SET category='' WHERE category IS NULL;")
            conn.execute("UPDATE stocks SET due_type='none' WHERE due_type IS NULL OR due_type='';")
            conn.execute("UPDATE stocks SET due_date='' WHERE due_date IS NULL;")
            conn.execute("UPDATE stocks SET item_kind='stock' WHERE item_kind IS NULL OR item_kind='';")
            conn.execute("UPDATE stocks SET created_at=? WHERE created_at IS NULL OR created_at='';", (_now(),))
            conn.execute("UPDATE stocks SET updated_at=? WHERE updated_at IS NULL OR updated_at='';", (_now(),))
            conn.commit()


# =========================
# CRUD / Queries
# =========================
def bulk_upsert(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Upsert items and sum qty when keys match.

    Key = (name_norm, category, item_kind, due_type, due_date, unit, subtype)

    Returns: {"inserted": x, "updated": y}
    """
    if not items:
        return {"inserted": 0, "updated": 0}

    inserted = 0
    updated = 0
    now = _now()

    with get_conn() as conn:
        for it in items:
            name = normalize_name(it.get("name", ""))
            if not name:
                continue
            name_norm = normalize_name(it.get("name_norm", "")) or normalize_name(name)

            category = normalize_category(it.get("category", ""))
            item_kind = normalize_item_kind(it.get("item_kind", "stock"))
            unit = _nfkc(it.get("unit", ""))
            subtype = _nfkc(it.get("subtype", ""))
            due_type = normalize_due_type(it.get("due_type", "none"))
            due_date = normalize_due_date(it.get("due_date", ""))
            memo = _nfkc(it.get("memo", ""))

            try:
                qty = float(it.get("qty", 0) or 0)
            except Exception:
                qty = 0.0

            exists = conn.execute(
                """
                SELECT 1 FROM stocks
                WHERE name_norm=? AND category=? AND item_kind=? AND due_type=? AND due_date=? AND unit=? AND subtype=?
                """,
                (name_norm, category, item_kind, due_type, due_date, unit, subtype),
            ).fetchone()

            conn.execute(
                """
                INSERT INTO stocks
                (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name_norm, category, item_kind, due_type, due_date, unit, subtype)
                DO UPDATE SET
                    qty = stocks.qty + excluded.qty,
                    memo = CASE
                        WHEN excluded.memo != '' THEN excluded.memo
                        ELSE stocks.memo
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    name,
                    name_norm,
                    qty,
                    unit,
                    category,
                    item_kind,
                    subtype,
                    due_type,
                    due_date,
                    memo,
                    now,
                    now,
                ),
            )

            if exists:
                updated += 1
            else:
                inserted += 1

        conn.commit()

    return {"inserted": inserted, "updated": updated}


def delete_stock(stock_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id=?;", (int(stock_id),))
        conn.commit()


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks;")
        conn.commit()


def get_category_stats(exclude_capacity: bool = True) -> Dict[str, Dict[str, float]]:
    """
    Return per-category stats:
      {category: {"rows": int, "qty": float}}
    """
    where = "WHERE item_kind != 'capacity'" if exclude_capacity else ""

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT category, COUNT(*) AS rows_count, COALESCE(SUM(qty),0) AS qty_sum
            FROM stocks
            {where}
            GROUP BY category
            """
        ).fetchall()

    out: Dict[str, Dict[str, float]] = {k: {"rows": 0, "qty": 0.0} for k in CANON_CATEGORIES}
    for r in rows:
        cat = normalize_category(r["category"])
        out.setdefault(cat, {"rows": 0, "qty": 0.0})
        out[cat]["rows"] += int(r["rows_count"] or 0)
        out[cat]["qty"] += float(r["qty_sum"] or 0.0)
    return out


def get_expiry_stats() -> Dict[str, int]:
    """
    Return counts for expiry:
      expired: due_date < today
      within30: today..today+30
      within90: today+31..today+90
    """
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT
              SUM(CASE WHEN due_type!='none' AND due_date!='' AND date(due_date) < date('now') THEN 1 ELSE 0 END) AS expired,
              SUM(CASE WHEN due_type!='none' AND due_date!='' AND date(due_date) >= date('now') AND date(due_date) <= date('now','+30 day') THEN 1 ELSE 0 END) AS within30,
              SUM(CASE WHEN due_type!='none' AND due_date!='' AND date(due_date) > date('now','+30 day') AND date(due_date) <= date('now','+90 day') THEN 1 ELSE 0 END) AS within90
            FROM stocks
            """
        ).fetchone()

    return {
        "expired": int(r["expired"] or 0),
        "within30": int(r["within30"] or 0),
        "within90": int(r["within90"] or 0),
    }


def list_by_category(category: str, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    cat = normalize_category(category)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM stocks
            WHERE category = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (cat, int(limit), int(offset)),
        ).fetchall()
    return [dict(r) for r in rows]


def count_by_category(category: str) -> int:
    cat = normalize_category(category)
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM stocks WHERE category=?;", (cat,)).fetchone()[0])


def export_all() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM stocks ORDER BY updated_at DESC, id DESC;").fetchall()
    return [dict(r) for r in rows]


def toilet_stats() -> Dict[str, Any]:
    """
    Convenience query for inspection:
      portable_uses: sum(qty) where unit in ('回','枚','袋','') and item_kind!='capacity'
      units_by_subtype: sum(qty) grouped by subtype (item_kind!='capacity')
    """
    with get_conn() as conn:
        portable = conn.execute(
            """
            SELECT COALESCE(SUM(qty),0) AS uses
            FROM stocks
            WHERE category='トイレ・衛生'
              AND item_kind!='capacity'
              AND COALESCE(unit,'') IN ('回','枚','袋','')
            """
        ).fetchone()
        by_sub = conn.execute(
            """
            SELECT subtype, COALESCE(SUM(qty),0) AS qty_sum
            FROM stocks
            WHERE category='トイレ・衛生' AND item_kind!='capacity'
            GROUP BY subtype
            """
        ).fetchall()

    units_by_subtype = {_nfkc(r["subtype"]): float(r["qty_sum"] or 0.0) for r in by_sub}
    return {
        "portable_uses": float(portable["uses"] or 0.0),
        "units_by_subtype": units_by_subtype,
    }
