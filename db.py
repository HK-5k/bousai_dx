import os
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# =========================================================
# Paths (VPS運用前提のデフォルト)
#   - 本番は /etc/bousai_dx.env で上書きする
#   - ローカル検証は環境変数で BOUSAI_DATA_DIR を切り替える
# =========================================================
DEFAULT_DATA_DIR = "/var/lib/bousai_dx"

def get_data_dir() -> str:
    return (os.environ.get("BOUSAI_DATA_DIR") or DEFAULT_DATA_DIR).strip()

def get_db_path() -> str:
    # 本番はSTOCK_DB_PATHで固定。未設定ならVPSデフォルトへ。
    p = os.environ.get("STOCK_DB_PATH")
    if p and p.strip():
        return p.strip()
    return str(Path(get_data_dir()) / "db" / "stock.db")

def get_photo_dir() -> str:
    p = os.environ.get("PHOTO_DIR")
    if p and p.strip():
        return p.strip()
    return str(Path(get_data_dir()) / "photos")

def _ensure_parent(path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def normalize_name(name: Any) -> str:
    return unicodedata.normalize("NFKC", str(name or "")).strip()

# =========================================================
# Connection (WAL + busy_timeout)
# =========================================================
def get_conn() -> sqlite3.Connection:
    db_path = _ensure_parent(get_db_path())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row

    # 重要: busy_timeoutは接続ごと
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")

    # WALはDBファイルに保持されるが、念のためここでも一度当てる
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn

def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols

# =========================================================
# Schema
# =========================================================
def init_db() -> None:
    with get_conn() as conn:
        # stocks
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

        # 旧DBからの移行：無い列だけ追加
        add_cols = [
            ("name_norm",  "ALTER TABLE stocks ADD COLUMN name_norm TEXT"),
            ("qty",        "ALTER TABLE stocks ADD COLUMN qty REAL DEFAULT 0"),
            ("unit",       "ALTER TABLE stocks ADD COLUMN unit TEXT DEFAULT ''"),
            ("category",   "ALTER TABLE stocks ADD COLUMN category TEXT"),
            ("item_kind",  "ALTER TABLE stocks ADD COLUMN item_kind TEXT DEFAULT 'stock'"),
            ("subtype",    "ALTER TABLE stocks ADD COLUMN subtype TEXT DEFAULT ''"),
            ("due_type",   "ALTER TABLE stocks ADD COLUMN due_type TEXT DEFAULT 'none'"),
            ("due_date",   "ALTER TABLE stocks ADD COLUMN due_date TEXT DEFAULT ''"),
            ("memo",       "ALTER TABLE stocks ADD COLUMN memo TEXT DEFAULT ''"),
            ("created_at", "ALTER TABLE stocks ADD COLUMN created_at TEXT"),
            ("updated_at", "ALTER TABLE stocks ADD COLUMN updated_at TEXT"),
        ]
        for col, ddl in add_cols:
            if not _has_column(conn, "stocks", col):
                conn.execute(ddl)

        # 写真（エビデンス）テーブル
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                rel_path TEXT NOT NULL,
                bytes INTEGER DEFAULT 0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                link_type TEXT NOT NULL,   -- 'stock' / 'ai' / 'inspection' など
                link_id TEXT NOT NULL,     -- stock_id or uuid
                created_at TEXT,
                FOREIGN KEY(photo_id) REFERENCES evidence_photos(id) ON DELETE CASCADE
            )
            """
        )

        # NULL正規化（古いDB対策）
        now = _now()
        conn.execute("UPDATE stocks SET unit='' WHERE unit IS NULL")
        conn.execute("UPDATE stocks SET subtype='' WHERE subtype IS NULL")
        conn.execute("UPDATE stocks SET due_type='none' WHERE due_type IS NULL OR due_type=''")
        conn.execute("UPDATE stocks SET due_date='' WHERE due_date IS NULL")
        conn.execute("UPDATE stocks SET item_kind='stock' WHERE item_kind IS NULL OR item_kind=''")
        conn.execute("UPDATE stocks SET memo='' WHERE memo IS NULL")
        conn.execute("UPDATE stocks SET created_at=? WHERE created_at IS NULL OR created_at=''", (now,))
        conn.execute("UPDATE stocks SET updated_at=? WHERE updated_at IS NULL OR updated_at=''", (now,))

        # name_norm埋め
        rows = conn.execute(
            "SELECT id, name FROM stocks WHERE name_norm IS NULL OR name_norm=''"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE stocks SET name_norm=? WHERE id=?",
                (normalize_name(r["name"]), r["id"]),
            )

        # index（高速化）
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_lookup
            ON stocks(name_norm, category, item_kind, due_type, due_date, unit, subtype)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_category_updated
            ON stocks(category, updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_photo_links_lookup
            ON photo_links(link_type, link_id)
            """
        )

        conn.commit()

# =========================================================
# Query APIs（全件取得を避ける）
# =========================================================
def get_category_agg() -> Dict[str, Dict[str, float]]:
    """
    categoryごとの
      - rows: レコード件数
      - qty_sum: 数量合計
    をSQLで集計して返す
    """
    out: Dict[str, Dict[str, float]] = {}
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(category,'') as category,
                   COUNT(*) as rows,
                   COALESCE(SUM(COALESCE(qty,0)),0) as qty_sum
            FROM stocks
            GROUP BY COALESCE(category,'')
            """
        ).fetchall()
        for r in rows:
            out[str(r["category"])] = {"rows": float(r["rows"]), "qty_sum": float(r["qty_sum"])}
    return out

def list_stocks_by_category(category: str, limit: int = 500) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT *
                FROM stocks
                WHERE COALESCE(category,'') = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (category, int(limit)),
            ).fetchall()
        ]

def count_expired(today_iso: str) -> int:
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT COUNT(*) as c
            FROM stocks
            WHERE due_date != ''
              AND substr(due_date, 1, 10) < ?
            """,
            (today_iso,),
        ).fetchone()
        return int(r["c"]) if r else 0

# =========================================================
# Upsert（安定）
# =========================================================
def bulk_upsert(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    同一キーなら qty 加算。キー：
      name_norm, category, item_kind, due_type, due_date, unit, subtype
    旧DBの UNIQUE(name,category,due_date) などが残っていても、
    IntegrityError時にフォールバックで更新する。
    """
    inserted = 0
    updated = 0
    now = _now()

    with get_conn() as conn:
        for it in items or []:
            name = normalize_name(it.get("name"))
            if not name:
                continue

            name_norm = normalize_name(name)
            category = str(it.get("category") or "").strip()
            unit = str(it.get("unit") or "").strip()
            item_kind = (str(it.get("item_kind") or "stock").strip().lower() or "stock")
            subtype = str(it.get("subtype") or "").strip()
            due_type = (str(it.get("due_type") or "none").strip().lower() or "none")
            due_date = str(it.get("due_date") or "").strip()
            memo = str(it.get("memo") or "").strip()

            try:
                qty = float(it.get("qty", 0) or 0)
            except Exception:
                qty = 0.0

            # 1) 新キーで UPDATE（qty加算）
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
                    AND COALESCE(category,'')=?
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

            # 2) INSERT
            try:
                conn.execute(
                    """
                    INSERT INTO stocks
                    (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, name_norm, qty, unit, category, item_kind, subtype, due_type, due_date, memo, now, now),
                )
                inserted += 1
                continue

            except sqlite3.IntegrityError:
                # 3) 旧制約救済（name+category+due_date）
                cur2 = conn.execute(
                    """
                    UPDATE stocks
                    SET
                        qty=COALESCE(qty,0) + ?,
                        updated_at=?
                    WHERE
                        name=?
                        AND COALESCE(category,'')=?
                        AND COALESCE(due_date,'')=?
                    """,
                    (qty, now, name, category, due_date),
                )
                if cur2.rowcount and cur2.rowcount > 0:
                    updated += 1
                    continue
                raise

        conn.commit()

    return {"inserted": inserted, "updated": updated}

# =========================================================
# Evidence photos
# =========================================================
def upsert_photo(sha256_hex: str, rel_path: str, bytes_: int, width: int, height: int) -> int:
    now = _now()
    with get_conn() as conn:
        r = conn.execute("SELECT id FROM evidence_photos WHERE sha256=?", (sha256_hex,)).fetchone()
        if r:
            return int(r["id"])
        conn.execute(
            """
            INSERT INTO evidence_photos(sha256, rel_path, bytes, width, height, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (sha256_hex, rel_path, int(bytes_ or 0), int(width or 0), int(height or 0), now),
        )
        conn.commit()
        rid = conn.execute("SELECT id FROM evidence_photos WHERE sha256=?", (sha256_hex,)).fetchone()
        return int(rid["id"]) if rid else 0

def link_photo(photo_id: int, link_type: str, link_id: str) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO photo_links(photo_id, link_type, link_id, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (int(photo_id), str(link_type), str(link_id), now),
        )
        conn.commit()

def list_photos_for(link_type: str, link_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT p.*
                FROM photo_links l
                JOIN evidence_photos p ON p.id = l.photo_id
                WHERE l.link_type=? AND l.link_id=?
                ORDER BY p.id DESC
                LIMIT ?
                """,
                (str(link_type), str(link_id), int(limit)),
            ).fetchall()
        ]

# =========================================================
# Maintenance
# =========================================================
def delete_stock(stock_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id=?", (int(stock_id),))
        conn.commit()

def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM photo_links")
        conn.execute("DELETE FROM evidence_photos")
        conn.execute("DELETE FROM stocks")
        conn.commit()
def get_all_stocks():
    """
    互換API: 旧app.pyが呼ぶ get_all_stocks を提供する。
    戻り値: stocks全行を list[dict] で返す。
    """
    with get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM stocks ORDER BY COALESCE(updated_at, created_at) DESC, id DESC"
            ).fetchall()
        except Exception:
            # 旧DB等で updated_at/created_at が無い場合のフォールバック
            rows = conn.execute("SELECT * FROM stocks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
