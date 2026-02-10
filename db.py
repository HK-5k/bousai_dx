import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# You can override DB file path by env var:
#   export STOCK_DB_PATH=/path/to/stock.db
DB_PATH = os.environ.get("STOCK_DB_PATH", "stock.db")

DATE_RE = re.compile(r"(\d{4})[\/\-\.\年](\d{1,2})[\/\-\.\月](\d{1,2})")

ALLOWED_DUE_TYPES = {"expiry", "inspection", "none"}
ALLOWED_ITEM_KINDS = {"stock", "capacity"}  # stock: 消耗品在庫 / capacity: 設備・能力（耐久財）

def _now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def normalize_key(name: str) -> str:
    return normalize_name(name).lower()

def normalize_unit(unit: str) -> str:
    u = unicodedata.normalize("NFKC", str(unit or "")).strip()
    u = re.sub(r"\s+", "", u)
    return u

def normalize_subtype(subtype: str) -> str:
    s = unicodedata.normalize("NFKC", str(subtype or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def extract_iso_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = map(int, m.groups())
    try:
        dt = datetime(y, mo, d).date()
    except ValueError:
        return ""
    return dt.isoformat()

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None

def _cols(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

def _create_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            qty REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            item_kind TEXT NOT NULL DEFAULT 'stock',
            subtype TEXT NOT NULL DEFAULT '',
            due_type TEXT NOT NULL DEFAULT 'none',
            due_date TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    cols = set(_cols(conn, "stocks"))
    if "unit" not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN unit TEXT NOT NULL DEFAULT '';")
    if "item_kind" not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN item_kind TEXT NOT NULL DEFAULT 'stock';")
    if "subtype" not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN subtype TEXT NOT NULL DEFAULT '';")

    conn.execute("UPDATE stocks SET unit='' WHERE unit IS NULL;")
    conn.execute("UPDATE stocks SET subtype='' WHERE subtype IS NULL;")
    conn.execute("UPDATE stocks SET item_kind='stock' WHERE item_kind IS NULL OR TRIM(item_kind)='';")
    conn.execute("UPDATE stocks SET unit='L' WHERE TRIM(unit)='' AND category LIKE '%水・飲料%';")
    conn.execute("UPDATE stocks SET unit='食' WHERE TRIM(unit)='' AND category LIKE '%主食類%';")
    conn.execute("UPDATE stocks SET unit='回' WHERE TRIM(unit)='' AND category LIKE '%トイレ%';")

def _dedupe(conn: sqlite3.Connection) -> None:
    now = _now_utc_iso()
    conn.execute("UPDATE stocks SET due_date='' WHERE due_date IS NULL;")
    conn.execute("UPDATE stocks SET due_type='none' WHERE due_type IS NULL OR TRIM(due_type)='';")
    conn.execute("UPDATE stocks SET memo='' WHERE memo IS NULL;")
    conn.execute("UPDATE stocks SET unit='' WHERE unit IS NULL;")
    conn.execute("UPDATE stocks SET subtype='' WHERE subtype IS NULL;")
    conn.execute("UPDATE stocks SET item_kind='stock' WHERE item_kind IS NULL OR TRIM(item_kind)='';")

    rows = conn.execute("SELECT id, name, name_norm FROM stocks WHERE name_norm IS NULL OR TRIM(name_norm)='';").fetchall()
    for r in rows:
        nn = normalize_key(r["name"])
        conn.execute("UPDATE stocks SET name_norm=? WHERE id=?", (nn, int(r["id"])))

    dup_groups = conn.execute(
        """
        SELECT name_norm, category, item_kind, subtype, due_type, due_date, unit,
               COUNT(*) AS cnt, SUM(qty) AS total_qty, MIN(id) AS keep_id
        FROM stocks
        GROUP BY name_norm, category, item_kind, subtype, due_type, due_date, unit
        HAVING cnt > 1
        """
    ).fetchall()

    for g in dup_groups:
        keep_id = int(g["keep_id"])
        total_qty = float(g["total_qty"] or 0)
        memos = conn.execute(
            """
            SELECT memo FROM stocks
            WHERE name_norm=? AND category=? AND item_kind=? AND subtype=? AND due_type=? AND due_date=? AND unit=?
            ORDER BY CASE WHEN TRIM(memo)='' THEN 1 ELSE 0 END, id ASC
            """,
            (g["name_norm"], g["category"], g["item_kind"], g["subtype"], g["due_type"], g["due_date"], g["unit"]),
        ).fetchall()
        memo_keep = ""
        for mr in memos:
            m = (mr["memo"] or "").strip()
            if m:
                memo_keep = m
                break
        conn.execute("UPDATE stocks SET qty=?, memo=?, updated_at=? WHERE id=?", (total_qty, memo_keep, now, keep_id))
        conn.execute(
            """
            DELETE FROM stocks
            WHERE name_norm=? AND category=? AND item_kind=? AND subtype=? AND due_type=? AND due_date=? AND unit=? AND id<>?
            """,
            (g["name_norm"], g["category"], g["item_kind"], g["subtype"], g["due_type"], g["due_date"], g["unit"], keep_id),
        )

def _ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_category ON stocks(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_kind ON stocks(item_kind);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_due ON stocks(due_type, due_date);")
    conn.execute("DROP INDEX IF EXISTS uq_stocks_key;")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_stocks_key ON stocks(name_norm, category, item_kind, subtype, due_type, due_date, unit);")
    except sqlite3.IntegrityError:
        pass

def init_db() -> None:
    with get_conn() as conn:
        if not _table_exists(conn, "stocks"):
            _create_table(conn)
            _ensure_indexes(conn)
            return
        cols = set(_cols(conn, "stocks"))
        required = {"id", "name", "name_norm", "qty", "category", "due_type", "due_date", "memo", "created_at", "updated_at"}
        if not required.issubset(cols):
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            legacy = f"stocks_legacy_{ts}"
            conn.execute(f"ALTER TABLE stocks RENAME TO {legacy};")
            _create_table(conn)
            rows = conn.execute(f"SELECT * FROM {legacy}").fetchall()
            now = _now_utc_iso()
            conn.execute("BEGIN")
            try:
                for r in rows:
                    rr = dict(r)
                    name = rr.get("name") or rr.get("item") or rr.get("product") or ""
                    qty = rr.get("qty") or 0
                    category = rr.get("category") or "その他"
                    memo = rr.get("memo") or ""
                    due_type = (rr.get("due_type") or "").strip().lower()
                    due_date = (rr.get("due_date") or "").strip()
                    if not due_date: due_date = extract_iso_date(memo)
                    if due_type not in ALLOWED_DUE_TYPES: due_type = "expiry" if due_date else "none"
                    created_at = rr.get("created_at") or now
                    updated_at = now
                    unit = rr.get("unit") or ""
                    item_kind = rr.get("item_kind") or rr.get("kind") or "stock"
                    subtype = rr.get("subtype") or rr.get("type") or ""
                    _upsert_in_conn(conn, name=name, qty=float(qty), unit=unit, category=category, item_kind=item_kind, subtype=subtype, due_type=due_type, due_date=due_date, memo=memo, created_at=created_at, updated_at=updated_at)
                _ensure_optional_columns(conn)
                _dedupe(conn)
                _ensure_indexes(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                conn.execute("DROP TABLE IF EXISTS stocks;")
                conn.execute(f"ALTER TABLE {legacy} RENAME TO stocks;")
                raise
            return
        conn.execute("BEGIN")
        try:
            _ensure_optional_columns(conn)
            _dedupe(conn)
            _ensure_indexes(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

def _normalize_due(due_type: str, due_date: str, memo: str) -> Tuple[str, str, str]:
    due_type = (due_type or "none").strip().lower()
    if due_type not in ALLOWED_DUE_TYPES: due_type = "none"
    due_date = str(due_date or "").strip()
    memo = str(memo or "")
    if due_type == "none": return due_type, "", memo
    if not due_date:
        parsed = extract_iso_date(memo)
        if parsed: return due_type, parsed, memo
        return due_type, "", memo
    iso = extract_iso_date(due_date)
    if iso: return due_type, iso, memo
    raw = due_date
    if raw:
        extra = f"(期限日付の形式が不明: {raw})"
        memo = (memo + "\n" + extra).strip() if memo else extra
    return due_type, "", memo

def _normalize_item_kind(kind: str) -> str:
    k = (kind or "stock").strip().lower()
    return k if k in ALLOWED_ITEM_KINDS else "stock"

def _upsert_in_conn(conn: sqlite3.Connection, *, name: str, qty: float, unit: str, category: str, item_kind: str, subtype: str, due_type: str, due_date: str, memo: str = "", created_at: Optional[str] = None, updated_at: Optional[str] = None) -> Tuple[str, int]:
    name = normalize_name(name)
    if not name: raise ValueError("name is empty")
    qty = float(qty or 0)
    if qty < 0: raise ValueError("qty must be >= 0")
    category = str(category or "その他").strip() or "その他"
    item_kind = _normalize_item_kind(item_kind)
    unit = normalize_unit(unit)
    subtype = normalize_subtype(subtype)
    due_type, due_date, memo = _normalize_due(due_type, due_date, memo)
    name_norm = normalize_key(name)
    created_at = created_at or _now_utc_iso()
    updated_at = updated_at or _now_utc_iso()
    row = conn.execute("SELECT id, qty, memo FROM stocks WHERE name_norm=? AND category=? AND item_kind=? AND subtype=? AND due_type=? AND due_date=? AND unit=?", (name_norm, category, item_kind, subtype, due_type, due_date, unit)).fetchone()
    if row:
        new_qty = float(row["qty"] or 0) + qty
        memo_keep = row["memo"] or ""
        if memo and not memo_keep: memo_keep = memo
        conn.execute("UPDATE stocks SET qty=?, memo=?, updated_at=? WHERE id=?", (new_qty, memo_keep, updated_at, int(row["id"])))
        return ("merged", int(row["id"]))
    cur = conn.execute("INSERT INTO stocks (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo or "", created_at, updated_at))
    return ("inserted", int(cur.lastrowid))

def upsert_stock(name: str, qty: float, category: str, *, unit: str = "", item_kind: str = "stock", subtype: str = "", due_type: str = "none", due_date: str = "", memo: str = "") -> Dict[str, Any]:
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            action, row_id = _upsert_in_conn(conn, name=name, qty=qty, unit=unit, category=category, item_kind=item_kind, subtype=subtype, due_type=due_type, due_date=due_date, memo=memo)
            conn.commit()
            return {"action": action, "id": row_id}
        except Exception:
            conn.rollback()
            raise

def bulk_upsert(items: List[Dict[str, Any]], *, atomic: bool = True) -> Dict[str, Any]:
    inserted = 0
    merged = 0
    errors: List[Dict[str, Any]] = []
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            for idx, it in enumerate(items):
                try:
                    action, _row_id = _upsert_in_conn(conn, name=it.get("name", ""), qty=float(it.get("qty", 0) or 0), unit=it.get("unit", ""), category=it.get("category", "その他"), item_kind=it.get("item_kind", it.get("kind", "stock")), subtype=it.get("subtype", ""), due_type=it.get("due_type", "none"), due_date=it.get("due_date", ""), memo=it.get("memo", ""))
                    if action == "inserted": inserted += 1
                    else: merged += 1
                except Exception as e:
                    errors.append({"index": idx, "error": str(e), "item": it})
                    if atomic: raise
            conn.commit()
        except Exception:
            conn.rollback()
            if atomic: return {"inserted": 0, "merged": 0, "errors": errors, "atomic": True}
            return {"inserted": inserted, "merged": merged, "errors": errors, "atomic": False}
    return {"inserted": inserted, "merged": merged, "errors": errors, "atomic": atomic}

def get_all_stocks() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at FROM stocks ORDER BY category, name_norm, item_kind, subtype, due_type, due_date, unit").fetchall()
        return [dict(r) for r in rows]

def delete_stock(stock_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id=?", (int(stock_id),))
        conn.commit()

def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks;")
        conn.commit()

def update_stock(stock_id: int, *, name: str, qty: float, unit: str, category: str, item_kind: str, subtype: str, due_type: str, due_date: str, memo: str) -> Dict[str, Any]:
    stock_id = int(stock_id)
    name = normalize_name(name)
    if not name: raise ValueError("name is empty")
    qty = float(qty or 0)
    if qty < 0: raise ValueError("qty must be >= 0")
    unit = normalize_unit(unit)
    category = str(category or "その他").strip() or "その他"
    item_kind = _normalize_item_kind(item_kind)
    subtype = normalize_subtype(subtype)
    due_type, due_date, memo = _normalize_due(due_type, due_date, memo)
    name_norm = normalize_key(name)
    now = _now_utc_iso()
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            target = conn.execute("SELECT id, qty FROM stocks WHERE name_norm=? AND category=? AND item_kind=? AND subtype=? AND due_type=? AND due_date=? AND unit=? AND id<>?", (name_norm, category, item_kind, subtype, due_type, due_date, unit, stock_id)).fetchone()
            if target:
                merged_qty = float(target["qty"] or 0) + qty
                conn.execute("UPDATE stocks SET qty=?, memo=?, updated_at=? WHERE id=?", (merged_qty, memo or "", now, int(target["id"])))
                conn.execute("DELETE FROM stocks WHERE id=?", (stock_id,))
                conn.commit()
                return {"action": "merged_on_update", "id": int(target["id"])}
            conn.execute("UPDATE stocks SET name=?, name_norm=?, qty=?, unit=?, category=?, item_kind=?, subtype=?, due_type=?, due_date=?, memo=?, updated_at=? WHERE id=?", (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo or "", now, stock_id))
            conn.commit()
            return {"action": "updated", "id": stock_id}
        except Exception:
            conn.rollback()
            raise

def get_recent_names(category: Optional[str] = None, limit: int = 30) -> List[str]:
    with get_conn() as conn:
        if category: rows = conn.execute("SELECT name FROM stocks WHERE category=? ORDER BY updated_at DESC LIMIT ?", (category, int(limit))).fetchall()
        else: rows = conn.execute("SELECT name FROM stocks ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
    seen = set()
    out: List[str] = []
    for r in rows:
        n = r["name"]
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out