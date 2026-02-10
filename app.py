import os
import re
import json
import ast
import uuid
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

try:
    import google.generativeai as genai
except Exception:
    genai = None

import db

# =========================
# App config
# =========================
APP_TITLE = "香川防災DX"
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL", "gemini-1.5-flash") or "gemini-1.5-flash").strip()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY and os.path.exists(".env"):
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                    GEMINI_API_KEY = line.split("=", 1)[1].strip().strip('"\'')
                    break
    except Exception:
        pass

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="collapsed",  # スマホでの左右ズレを減らす
)

# =========================
# Session state
# =========================
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss_init("current_page", "home")
ss_init("inv_cat", None)
ss_init("pending_items", [])
ss_init("undo_stack", [])
ss_init("ai_last_raw", "")

def navigate_to(page: str) -> None:
    st.session_state.current_page = page
    st.rerun()

# =========================
# Constants
# =========================
CATEGORIES: Dict[str, str] = {
    "水・飲料": "💧",
    "主食類": "🍚",
    "トイレ・衛生": "🚽",
    "乳幼児用品": "👶",
    "寝具・避難": "🛏️",
    "資機材": "🔋",
    "その他": "📦",
}

DUE_LABEL = {"expiry": "賞味期限", "inspection": "点検日", "none": "期限なし"}
ITEM_KIND_LABEL = {"stock": "在庫（消耗品）", "capacity": "設備能力（耐久財）"}

TOILET_SUBTYPES = [
    "携帯トイレ",
    "組立トイレ",
    "仮設トイレ",
    "トイレ袋",
    "凝固剤",
    "その他",
]

# 主要カテゴリの「評価用」基準単位
BASE_UNIT = {"水・飲料": "L", "主食類": "食", "トイレ・衛生": "回"}

# =========================
# CSS: 全ページ中央寄せ・スマホ最適化
# =========================
st.markdown(
    """
<style>
.stApp { background-color: #f8fafc; }

/* 全ページのメインコンテンツを中央寄せ */
.block-container {
    max-width: 600px !important;
    margin: 0 auto !important;
    padding: 1rem 1rem 2rem 1rem !important;
}

h2, h3 {
    text-align: center;
    font-weight: 900;
    color: #0f172a;
}

/* スマホでも2列を維持（トップ/カテゴリタイル用） */
[data-testid="stHorizontalBlock"] {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    gap: 12px !important;
}
[data-testid="stHorizontalBlock"] > div {
    flex: 1 1 0% !important;
    min-width: 0 !important;
}

/* 通常ボタン（小さめ） */
div.stButton > button {
    width: 100% !important;
    height: 48px !important;
    border-radius: 12px !important;
    font-weight: 800 !important;
}

/* タイル用：primaryボタンだけ大きく */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="baseButton-primary"] {
    height: 150px !important;
    border-radius: 22px !important;
    border: 1px solid #e2e8f0 !important;
    background: #ffffff !important;
    box-shadow: 0 10px 15px -3px rgba(0,0,0,0.10) !important;
    font-size: 1.05rem !important;
    white-space: pre-wrap !important;
}

/* タイル押下時 */
div.stButton > button[kind="primary"]:active,
div.stButton > button[data-testid="baseButton-primary"]:active {
    transform: scale(0.95) !important;
}

/* スコア表示（ドーナツ） */
.score-circle {
    width: 155px; height: 155px; border-radius: 50%;
    background: conic-gradient(#3b82f6 var(--p), #e2e8f0 0deg);
    display: flex; align-items: center; justify-content: center;
    margin: 0.5rem auto 1rem auto;
    font-size: 2.6rem; font-weight: 900; color: #0f172a;
    position: relative;
}
.score-circle::after { content: attr(data-score); position: absolute; }

/* バッジ */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    background: #eef2ff;
    color: #1e40af;
    font-weight: 900;
    font-size: 0.78rem;
}

/* カード */
.card {
    background: #fff;
    border-radius: 14px;
    padding: 12px 14px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    border-left: 7px solid #cbd5e1;
    margin: 10px 0;
}
.card-ok { border-left-color: #22c55e; }
.card-ng { border-left-color: #ef4444; }
.card-warn { border-left-color: #f59e0b; }

#MainMenu {visibility:hidden;}
footer {visibility:hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# =========================
# Sidebar: 備蓄想定設定
# =========================
with st.sidebar:
    st.header("⚙️ 備蓄設定")
    t_pop = st.number_input("想定人数 (人)", 1, 1_000_000, 100, 100)
    t_days = st.slider("目標備蓄日数 (日)", 1, 7, 3)
    st.info(f"目標: {t_pop:,}人 × {t_days}日分")

TARGETS = {
    "水・飲料": t_pop * 3 * t_days,     # 3L/人/日
    "主食類": t_pop * 3 * t_days,       # 3食/人/日
    "トイレ・衛生": t_pop * 5 * t_days, # 5回/人/日
}

# =========================
# Utilities
# =========================
def toast(msg: str, icon: str = "") -> None:
    try:
        st.toast(msg, icon=icon)  # type: ignore[attr-defined]
    except Exception:
        if icon:
            st.success(f"{icon} {msg}")
        else:
            st.success(msg)

def get_cat_key(cat: Any) -> str:
    s = str(cat or "")
    for k in CATEGORIES.keys():
        if k in s:
            return k
    return "その他"

def iso_to_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        pass
    # fallback: yyyy/mm/dd or yyyy年mm月dd日
    m = re.search(r"(\d{4})[\/\-\.\年](\d{1,2})[\/\-\.\月](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    try:
        return date(y, mo, d)
    except Exception:
        return None

def add_years_safe(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + years)

def due_label(due_type: str, due_date: str) -> str:
    t = (due_type or "none").lower()
    if t == "none":
        return "期限なし"
    dd = due_date or "未設定"
    return f"{DUE_LABEL.get(t,t)}: {dd}"

def due_badge(due_type: str, due_date: str, today_: date) -> Tuple[str, str]:
    t = (due_type or "none").lower()
    if t == "none":
        return ("期限なし", "none")
    dt = iso_to_date(due_date)
    if not dt:
        return ("日付未設定", "warn")
    if dt < today_:
        return ("期限切れ", "danger")
    if dt <= today_ + timedelta(days=30):
        return ("30日以内", "warn")
    if dt <= today_ + timedelta(days=90):
        return ("90日以内", "warn")
    return ("OK", "ok")

def infer_toilet_subtype(name: str) -> str:
    n = str(name or "")
    if "仮設" in n:
        return "仮設トイレ"
    if "組立" in n:
        return "組立トイレ"
    if "携帯" in n:
        return "携帯トイレ"
    if "凝固" in n:
        return "凝固剤"
    if "袋" in n or "便袋" in n:
        return "トイレ袋"
    return "その他"

def _norm_unit(u: str) -> str:
    return re.sub(r"\s+", "", (u or "").strip())

def convert_water_to_liters(qty: float, unit: str, name: str, memo: str) -> float:
    """
    水・飲料(在庫)は L に統一して保存・評価する。
    許容単位: L, ml, m3, 本, 箱/ケース
    - 本: 品名/メモから 500ml, 2L 等の容量が読めること
    - 箱/ケース: 品名/メモから 24本 等の入数 + 容量が読めること
    """
    u = _norm_unit(unit).lower()
    q = float(qty or 0)

    if u in {"", "l", "ℓ", "ｌ", "リットル"}:
        return q
    if u in {"ml", "ｍｌ", "milliliter"}:
        return q / 1000.0
    if u in {"m3", "㎥", "m^3", "立方メートル"}:
        return q * 1000.0

    text = f"{name} {memo}"
    # 容量（ml/L）を抽出
    vol_l: Optional[float] = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|ｍｌ)", text, flags=re.IGNORECASE)
    if m:
        vol_l = float(m.group(1)) / 1000.0
    else:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*(l|ℓ|ｌ)", text, flags=re.IGNORECASE)
        if m2:
            vol_l = float(m2.group(1))

    if u in {"本", "ぼん", "ボトル", "缶", "個"}:
        if not vol_l:
            raise ValueError("水の単位が本/個ですが、品名/メモから容量(ml/L)が読めません")
        return q * vol_l

    if u in {"箱", "ケース", "case"}:
        if not vol_l:
            raise ValueError("水の単位が箱/ケースですが、品名/メモから容量(ml/L)が読めません")
        # 入数（24本など）
        count: Optional[int] = None
        m3 = re.search(r"[×xX＊*]\s*(\d+)\s*本", text)
        if m3:
            count = int(m3.group(1))
        else:
            m4 = re.search(r"(\d+)\s*本入", text)
            if m4:
                count = int(m4.group(1))
            else:
                m5 = re.search(r"(\d+)\s*入り", text)
                if m5:
                    count = int(m5.group(1))
        if not count:
            raise ValueError("箱/ケースですが、品名/メモから入数（例: ×24本 / 24本入）が読めません")
        return q * float(count) * float(vol_l)

    raise ValueError(f"水の単位 '{unit}' をL換算できません（推奨: L / ml / m3 / 本 / ケース）")

def convert_food_to_meals(qty: float, unit: str, name: str, memo: str) -> float:
    """
    主食類(在庫)は '食' に統一して保存・評価する。
    許容単位: 食, 箱/袋/ケース（品名に '◯食' が含まれる場合のみ換算）
    """
    u = _norm_unit(unit)
    q = float(qty or 0)
    if u in {"", "食"}:
        return q

    text = f"{name} {memo}"
    m = re.search(r"(\d+)\s*食", text)
    if m and u in {"箱", "袋", "ケース"}:
        per = int(m.group(1))
        return q * float(per)

    raise ValueError(f"主食類の単位 '{unit}' を食に換算できません（推奨: 食 / 例: '50食' を品名に含める）")

def toilet_uses_from_unit(qty: float, unit: str) -> Optional[float]:
    u = _norm_unit(unit)
    q = float(qty or 0)
    if u in {"", "回"}:
        return q
    if u in {"枚", "袋"}:
        return q  # 1枚=1回, 1袋=1回 として扱う（現場でルール徹底推奨）
    # 基/台などは「回換算できない（設備）」として別枠表示
    return None

# =========================
# DB load + aggregation
# =========================
db.init_db()
stocks: List[Dict[str, Any]] = db.get_all_stocks() or []
today = datetime.now().date()

# amount aggregation: 主要カテゴリは基準単位に換算して集計（設備能力は除外）
amounts: Dict[str, float] = {k: 0.0 for k in CATEGORIES}
unit_issues: List[Dict[str, Any]] = []
water_capacity: List[Dict[str, Any]] = []

for s in stocks:
    cat_key = get_cat_key(s.get("category"))
    item_kind = str(s.get("item_kind") or "stock").strip().lower()
    qty = float(s.get("qty") or 0)
    unit = str(s.get("unit") or "").strip()

    if item_kind == "capacity":
        if cat_key == "水・飲料":
            water_capacity.append(s)
        continue

    # stock
    try:
        if cat_key == "水・飲料":
            amounts[cat_key] += convert_water_to_liters(qty, unit or "L", s.get("name", ""), s.get("memo", ""))
        elif cat_key == "主食類":
            amounts[cat_key] += convert_food_to_meals(qty, unit or "食", s.get("name", ""), s.get("memo", ""))
        elif cat_key == "トイレ・衛生":
            uses = toilet_uses_from_unit(qty, unit or "回")
            if uses is not None:
                amounts[cat_key] += uses
        else:
            amounts[cat_key] += qty
    except Exception as e:
        unit_issues.append({"id": s.get("id"), "category": cat_key, "name": s.get("name"), "qty": qty, "unit": unit, "error": str(e)})

# 期限アラート（在庫・設備とも対象）
expired_count = soon30_count = soon90_count = 0
for s in stocks:
    if (s.get("due_type") or "none") == "none":
        continue
    dt = iso_to_date(str(s.get("due_date") or ""))
    if not dt:
        continue
    if dt < today:
        expired_count += 1
    elif dt <= today + timedelta(days=30):
        soon30_count += 1
    elif dt <= today + timedelta(days=90):
        soon90_count += 1

# =========================
# Gemini helpers
# =========================
def extract_items_from_gemini(text: str) -> List[Dict[str, Any]]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\[[\s\S]*\]", t)
    payload = m.group(0) if m else t

    try:
        data = json.loads(payload)
    except Exception:
        data = ast.literal_eval(payload)

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("AI出力がJSON配列ではありません")

    out: List[Dict[str, Any]] = []
    for x in data:
        if not isinstance(x, dict):
            continue

        name = str(x.get("name") or x.get("item") or "").strip()
        if not name:
            continue

        # qty can be float (e.g. 12.0 L)
        qty_raw = x.get("qty", 1)
        try:
            qty = float(qty_raw)
        except Exception:
            try:
                qty = float(str(qty_raw).replace(",", ""))
            except Exception:
                qty = 1.0

        unit = str(x.get("unit") or "").strip()

        item_kind = str(x.get("item_kind") or x.get("kind") or "stock").strip().lower()
        if item_kind not in {"stock", "capacity"}:
            item_kind = "stock"

        subtype = str(x.get("subtype") or "").strip()

        due_type = str(x.get("due_type") or "none").strip().lower()
        if due_type in {"賞味期限", "期限", "expiry"}:
            due_type = "expiry"
        elif due_type in {"点検", "点検日", "inspection"}:
            due_type = "inspection"
        elif due_type in {"none", "なし", "期限なし"}:
            due_type = "none"
        else:
            due_type = "none"

        due_date_raw = str(x.get("due_date") or "").strip()
        due_date_iso = ""
        if due_type != "none" and due_date_raw:
            dt = iso_to_date(due_date_raw)
            due_date_iso = dt.isoformat() if dt else ""

        memo = str(x.get("memo") or "").strip()

        out.append(
            {
                "name": name,
                "qty": qty,
                "unit": unit,
                "item_kind": item_kind,
                "subtype": subtype,
                "due_type": due_type,
                "due_date": due_date_iso,
                "memo": memo,
            }
        )
    return out

@st.cache_resource(show_spinner=False)
def get_gemini_model(api_key: str, model_name: str):
    if genai is None:
        raise RuntimeError("google-generativeai がありません")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)

def gemini_extract_from_image(image_file, category: str) -> Tuple[List[Dict[str, Any]], str]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY が未設定です")

    model = get_gemini_model(GEMINI_API_KEY, GEMINI_MODEL)

    prompt = f"""
あなたは「防災備蓄 台帳」の抽出エンジンです。
返答は **必ず JSON配列のみ**（説明文・Markdown禁止）。

カテゴリ: "{category}"

各要素は次の形にしてください:
{{
  "name": "品名",
  "qty": 1,
  "unit": "L|食|回|本|ケース|基|台など",
  "item_kind": "stock|capacity",
  "subtype": "携帯トイレ|組立トイレ|仮設トイレ|トイレ袋|凝固剤|その他 (トイレ以外は空でOK)",
  "due_type": "expiry|inspection|none",
  "due_date": "YYYY-MM-DD もしくは ''",
  "memo": "任意"
}}

重要ルール:
- 台帳のデータ品質を優先。曖昧な推測はしない。
- qtyは数値。読めなければ 1。
- 日付が不明なら due_type="none", due_date=""。
- 年月のみ等で日が不明なら due_date="" にして memo に残す。
- 「水・飲料」の場合:
  - 消耗品在庫は item_kind="stock" を基本
  - 造水機/貯水槽など設備・能力は item_kind="capacity"
  - stock は L に寄せる（例: 500ml×24本 → qty=12, unit="L" が理想。ただし無理なら unit="本"/"ケース" でもOK）
- 「トイレ・衛生」の場合:
  - subtype を必ず選ぶ（上の候補から）
"""

    pil = Image.open(image_file)
    res = model.generate_content([prompt, pil])
    raw = getattr(res, "text", "") or ""
    items = extract_items_from_gemini(raw)
    return items, raw

# =========================
# Cart helpers
# =========================
def _default_unit_for(cat_key: str, item_kind: str, subtype: str) -> str:
    if item_kind == "capacity":
        return "台"
    if cat_key in BASE_UNIT:
        if cat_key == "トイレ・衛生":
            if subtype in {"仮設トイレ", "組立トイレ"}:
                return "基"
            return "回"
        return BASE_UNIT[cat_key]
    return "点"

def _canonicalize_cart_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """
    カートに入る時点で、主要カテゴリ(stock)は基準単位へ寄せる。
    変換できない場合は needs_fix=True を付ける（登録ブロック用）。
    """
    name = db.normalize_name(it.get("name", ""))
    cat = str(it.get("category") or "その他").strip() or "その他"
    cat_key = get_cat_key(cat)
    item_kind = str(it.get("item_kind") or "stock").strip().lower()
    if item_kind not in {"stock", "capacity"}:
        item_kind = "stock"

    subtype = str(it.get("subtype") or "").strip()
    if cat_key == "トイレ・衛生" and not subtype:
        subtype = infer_toilet_subtype(name)
    if cat_key == "トイレ・衛生" and subtype not in TOILET_SUBTYPES:
        subtype = "その他"

    try:
        qty = float(it.get("qty", 1) or 0)
    except Exception:
        qty = 1.0
    if qty < 0:
        qty = 0.0

    unit = str(it.get("unit") or "").strip()
    if not unit:
        unit = _default_unit_for(cat_key, item_kind, subtype)

    due_type = str(it.get("due_type") or "none").strip().lower()
    due_date = str(it.get("due_date") or "").strip()
    memo = str(it.get("memo") or "").strip()

    needs_fix = False
    fix_reason = ""

    # Normalize: none -> empty date
    if due_type == "none":
        due_date = ""

    # Standardize key categories (stock only)
    if item_kind == "stock":
        try:
            if cat_key == "水・飲料":
                liters = convert_water_to_liters(qty, unit, name, memo)
                qty, unit = liters, "L"
            elif cat_key == "主食類":
                meals = convert_food_to_meals(qty, unit, name, memo)
                qty, unit = meals, "食"
            elif cat_key == "トイレ・衛生":
                uses = toilet_uses_from_unit(qty, unit)
                if uses is not None:
                    qty, unit = uses, "回"
                # 基などはそのまま（回に換算できない）
        except Exception as e:
            needs_fix = True
            fix_reason = str(e)

    out = {
        "id": it.get("id") or uuid.uuid4().hex,
        "name": name,
        "qty": qty,
        "unit": unit,
        "category": cat,
        "item_kind": item_kind,
        "subtype": subtype,
        "due_type": due_type,
        "due_date": due_date,
        "memo": memo,
        "needs_fix": bool(needs_fix),
        "fix_reason": fix_reason,
    }
    return out

def cart_key(it: Dict[str, Any]) -> Tuple[str, str, str, str, str, str, str]:
    return (
        db.normalize_name(it.get("name", "")).lower(),
        str(it.get("category", "")).strip(),
        str(it.get("item_kind", "stock")).lower(),
        str(it.get("subtype", "")).strip(),
        str(it.get("due_type", "none")).lower(),
        str(it.get("due_date", "")).strip(),
        str(it.get("unit", "")).strip(),
    )

def cart_add(item: Dict[str, Any]) -> None:
    it = _canonicalize_cart_item(item)

    k = cart_key(it)
    for ex in st.session_state.pending_items:
        if cart_key(ex) == k and (not ex.get("needs_fix")) and (not it.get("needs_fix")):
            ex["qty"] = float(ex.get("qty", 0) or 0) + float(it.get("qty", 0) or 0)
            if it.get("memo") and not ex.get("memo"):
                ex["memo"] = it.get("memo")
            return

    st.session_state.pending_items.append(it)

def cart_remove(item_id: str) -> None:
    for i, it in enumerate(list(st.session_state.pending_items)):
        if it.get("id") == item_id:
            st.session_state.undo_stack.append(st.session_state.pending_items.pop(i))
            return

def cart_duplicate(item_id: str) -> None:
    for it in st.session_state.pending_items:
        if it.get("id") == item_id:
            dup = dict(it)
            dup["id"] = uuid.uuid4().hex
            st.session_state.pending_items.append(dup)
            return

def cart_undo() -> None:
    if st.session_state.undo_stack:
        st.session_state.pending_items.append(st.session_state.undo_stack.pop())

def cart_merge_duplicates() -> None:
    merged: Dict[Tuple[str, str, str, str, str, str, str], Dict[str, Any]] = {}
    for it in st.session_state.pending_items:
        k = cart_key(it)
        if k not in merged:
            merged[k] = dict(it)
        else:
            # needs_fix が混ざる場合は安全のため統合しない
            if merged[k].get("needs_fix") or it.get("needs_fix"):
                continue
            merged[k]["qty"] = float(merged[k].get("qty", 0) or 0) + float(it.get("qty", 0) or 0)
            if not merged[k].get("memo") and it.get("memo"):
                merged[k]["memo"] = it.get("memo")
    st.session_state.pending_items = list(merged.values())

def validate_items_for_commit(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    payload: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for it in items:
        name = db.normalize_name(it.get("name", ""))
        if not name:
            errors.append({"id": it.get("id"), "error": "品名が空です"})
            continue

        cat = str(it.get("category") or "その他").strip() or "その他"
        cat_key = get_cat_key(cat)
        item_kind = str(it.get("item_kind") or "stock").strip().lower()
        if item_kind not in {"stock", "capacity"}:
            item_kind = "stock"

        subtype = str(it.get("subtype") or "").strip()
        unit = str(it.get("unit") or "").strip()
        due_type = str(it.get("due_type") or "none").strip().lower()
        due_date = str(it.get("due_date") or "").strip()
        memo = str(it.get("memo") or "").strip()

        try:
            qty = float(it.get("qty") or 0)
        except Exception:
            qty = 0.0
        if qty < 0:
            errors.append({"id": it.get("id"), "name": name, "error": "数量が負数です"})
            continue

        if it.get("needs_fix"):
            errors.append({"id": it.get("id"), "name": name, "error": it.get("fix_reason") or "単位換算が必要です"})
            continue

        # 主要カテゴリ(stock)は基準単位を強制（DB側の整合性・合算を担保）
        if item_kind == "stock" and cat_key in BASE_UNIT:
            base = BASE_UNIT[cat_key]
            if not unit:
                unit = base

            if cat_key == "トイレ・衛生":
                # 「回」(消耗品) と 「基」(仮設/組立など設備寄り) を許容
                if unit not in {"回", "基"}:
                    errors.append({"id": it.get("id"), "name": name, "error": f"トイレ・衛生 (stock) の単位は 回 または 基 を推奨（現在: {unit}）"})
                    continue
            else:
                if unit != base:
                    errors.append({"id": it.get("id"), "name": name, "error": f"{cat_key} (stock) は単位 {base} で登録してください（現在: {unit}）"})
                    continue

        payload.append(
            {
                "name": name,
                "qty": qty,
                "unit": unit,
                "category": cat,
                "item_kind": item_kind,
                "subtype": subtype,
                "due_type": due_type,
                "due_date": due_date,
                "memo": memo,
            }
        )

    return payload, errors

def cart_commit(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload, errors = validate_items_for_commit(items)
    if errors:
        return {"inserted": 0, "merged": 0, "errors": errors, "atomic": True}
    return db.bulk_upsert(payload, atomic=True) if payload else {"inserted": 0, "merged": 0, "errors": [], "atomic": True}

# =========================
# UI helpers
# =========================
def back_to_home(key: str) -> None:
    if st.button("🔙 ホームに戻る", key=key, type="secondary"):
        st.session_state.inv_cat = None
        navigate_to("home")

def render_due_inputs(prefix: str, default_due_type: str, default_due_date_iso: str) -> Tuple[str, str]:
    due_type_key = f"{prefix}_due_type"
    due_date_key = f"{prefix}_due_date"

    ss_init(due_type_key, default_due_type or "none")
    ss_init(due_date_key, iso_to_date(default_due_date_iso) or today)

    due_type = st.radio(
        "期限種別",
        options=["expiry", "inspection", "none"],
        horizontal=True,
        format_func=lambda x: DUE_LABEL[x],
        key=due_type_key,
    )

    if due_type == "none":
        st.caption("期限なし: 日付は保存されません。")
        return due_type, ""

    c1, c2, c3 = st.columns(3)
    with c1:
        st.button("+1年", key=f"{prefix}_p1", type="secondary", on_click=lambda k=due_date_key: st.session_state.__setitem__(k, add_years_safe(today, 1)))
    with c2:
        st.button("+3年", key=f"{prefix}_p3", type="secondary", on_click=lambda k=due_date_key: st.session_state.__setitem__(k, add_years_safe(today, 3)))
    with c3:
        st.button("+5年", key=f"{prefix}_p5", type="secondary", on_click=lambda k=due_date_key: st.session_state.__setitem__(k, add_years_safe(today, 5)))

    d = st.date_input("日付", key=due_date_key)
    return due_type, (d.isoformat() if isinstance(d, date) else "")

def fmt_qty(q: float) -> str:
    # Display: integer if close to int
    try:
        if abs(q - round(q)) < 1e-9:
            return f"{int(round(q)):,}"
        return f"{q:,.2f}"
    except Exception:
        return str(q)

# =========================
# Pages
# =========================
def page_home() -> None:
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown("<p style='text-align:center; color:#64748b; margin-top:-12px;'>物資DX台帳 × 自主点検システム</p>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊\n分析レポート\n(充足率)", key="home_dash", type="primary"):
            navigate_to("dashboard")
        if st.button("✅\n自動自主点検\n(裏取り)", key="home_insp", type="primary"):
            navigate_to("inspection")
    with c2:
        if st.button("📦\n備蓄・登録\n(現場)", key="home_inv", type="primary"):
            navigate_to("inventory")
        if st.button("💾\nデータ管理\n(CSV)", key="home_data", type="primary"):
            navigate_to("data")

    st.markdown("---")

    if expired_count:
        st.error(f"🚨 期限切れ **{expired_count}件** があります")
    elif soon30_count:
        st.warning(f"⚠️ 30日以内に期限が来るものが **{soon30_count}件** あります")
    elif soon90_count:
        st.info(f"ℹ️ 90日以内に期限が来るものが **{soon90_count}件** あります")
    else:
        st.success("✅ 期限切れ・期限接近は検出されませんでした")

    m1, m2, m3 = st.columns(3)
    m1.metric("期限切れ", f"{expired_count}件")
    m2.metric("30日以内", f"{soon30_count}件")
    m3.metric("90日以内", f"{soon90_count}件")

    st.markdown("### 主要3カテゴリ 充足状況（設備能力は別枠）")
    for k, unit in [("水・飲料", "L"), ("主食類", "食"), ("トイレ・衛生", "回")]:
        have = float(amounts.get(k, 0))
        need = float(TARGETS.get(k, 0) or 0)
        pct = (have / need) if need > 0 else 0.0
        shortage = max(0.0, need - have)
        st.write(f"**{CATEGORIES[k]} {k}**  現在 {fmt_qty(have)}{unit} / 目標 {fmt_qty(need)}{unit}（{int(pct*100)}%） 不足 {fmt_qty(shortage)}{unit}")
        st.progress(min(pct, 1.0))

    if water_capacity:
        st.caption(f"参考: 💧 設備能力（耐久財） {len(water_capacity)}件はスコアに含めず別表示しています。")

    if unit_issues:
        with st.expander(f"⚠️ 単位/換算の問題: {len(unit_issues)}件（スコア集計から除外）", expanded=False):
            st.json(unit_issues[:20])

def page_dashboard() -> None:
    back_to_home("dash_back")

    st.markdown("## 📊 充足率レポート")
    st.caption(f"目標: {t_pop:,}人 × {t_days}日分（設備能力は別枠）")

    r_w = min(amounts["水・飲料"] / (TARGETS["水・飲料"] or 1), 1.0)
    r_f = min(amounts["主食類"] / (TARGETS["主食類"] or 1), 1.0)
    r_t = min(amounts["トイレ・衛生"] / (TARGETS["トイレ・衛生"] or 1), 1.0)
    score = int(((r_w + r_f + r_t) / 3) * 100)

    color = "#22c55e" if score >= 80 else "#f59e0b" if score >= 50 else "#ef4444"
    st.markdown(
        f'<div class="score-circle" style="--p:{score*3.6}deg; background: conic-gradient({color} var(--p), #e2e8f0 0deg);" data-score="{score}%"></div>',
        unsafe_allow_html=True,
    )

    for k, icon, unit in [("水・飲料", "💧", "L"), ("主食類", "🍚", "食"), ("トイレ・衛生", "🚽", "回")]:
        have = float(amounts[k])
        need = float(TARGETS[k]) if TARGETS[k] else 0.0
        pct = (have / need) if need > 0 else 0.0
        st.write(f"**{icon} {k}**")
        st.progress(min(pct, 1.0))
        st.caption(f"現在 {fmt_qty(have)}{unit} / 目標 {fmt_qty(need)}{unit}（{int(pct*100)}%）")

    st.markdown("---")
    st.markdown("### 💧 飲料水の内訳（在庫 vs 設備能力）")

    st.markdown(
        f"""
<div class="card">
  <div style="font-weight:900;">在庫（消耗品）</div>
  <div style="color:#475569; margin-top:2px;">スコア対象: {fmt_qty(amounts['水・飲料'])}L</div>
</div>
""",
        unsafe_allow_html=True,
    )

    if water_capacity:
        st.markdown(
            f"""
<div class="card card-warn">
  <div style="font-weight:900;">設備能力（耐久財）</div>
  <div style="color:#475569; margin-top:2px;">スコア対象外: {len(water_capacity)}件</div>
</div>
""",
            unsafe_allow_html=True,
        )
        with st.expander("設備能力の一覧（合算しません）", expanded=False):
            df = pd.DataFrame(water_capacity)[["name", "qty", "unit", "due_type", "due_date", "memo"]].copy()
            df.rename(columns={"name": "品名", "qty": "数量", "unit": "単位", "due_type": "期限種別", "due_date": "日付", "memo": "メモ"}, inplace=True)
            st.dataframe(df, use_container_width=True)
    else:
        st.info("設備能力（造水機/貯水槽 等）はまだ登録されていません。")

    st.markdown("---")
    st.markdown("### 期限が近いもの（上位10件）")
    soon: List[Tuple[date, Dict[str, Any]]] = []
    for s in stocks:
        if (s.get("due_type") or "none") == "none":
            continue
        dt = iso_to_date(str(s.get("due_date") or ""))
        if dt:
            soon.append((dt, s))
    soon.sort(key=lambda x: x[0])
    if not soon:
        st.info("期限管理されている在庫がありません。")
        return

    for dt, s in soon[:10]:
        cat = get_cat_key(s.get("category"))
        badge, kind = due_badge(s.get("due_type"), s.get("due_date"), today)
        icon = "🚨" if kind == "danger" else "⚠️" if kind == "warn" else "✅"
        knd = str(s.get("item_kind") or "stock")
        knd_txt = ITEM_KIND_LABEL.get(knd, knd)
        st.markdown(
            f"{icon} **{CATEGORIES.get(cat,'📦')} {cat}** / **{s.get('name')}** "
            f"×{fmt_qty(float(s.get('qty') or 0))}{s.get('unit','')} "
            f"({knd_txt}) / {due_label(s.get('due_type'), s.get('due_date'))}  "
            f"<span class='badge'>{badge}</span>",
            unsafe_allow_html=True,
        )

    if unit_issues:
        with st.expander(f"⚠️ 単位/換算の問題: {len(unit_issues)}件（要修正）", expanded=False):
            st.json(unit_issues[:50])

def page_inspection() -> None:
    back_to_home("insp_back")
    st.markdown("## ✅ 自動点検判定")
    st.info("台帳データから、自主点検の一部項目を自動判定します（目標日数ベース）。")

    def card(code: str, title: str, ok: bool, evidence: str) -> None:
        cls = "card-ok" if ok else "card-ng"
        status = "🟢 適合 (○)" if ok else "🔴 不適合 (×)"
        st.markdown(
            f"""
<div class="card {cls}">
  <div style="color:#64748b; font-size:0.85rem; font-weight:900;">点検項目 {code}</div>
  <div style="font-weight:900; color:#0f172a; margin-top:2px;">{title}</div>
  <div style="margin-top:6px; font-weight:900;">判定: {status}</div>
  <div style="margin-top:4px; color:#475569; font-size:0.9rem; white-space: pre-wrap;">証跡: {evidence}</div>
</div>
""",
            unsafe_allow_html=True,
        )

    def ev(have: float, need: float, unit: str) -> str:
        pct = int(have / need * 100) if need > 0 else 0
        short = max(0.0, need - have)
        return f"充足率 {pct}% / 不足 {fmt_qty(short)}{unit}"

    # 7-1 水（在庫のみ）
    card(
        "7-1(水)",
        "避難想定人数に対する飲料水の備蓄（在庫）",
        amounts["水・飲料"] >= TARGETS["水・飲料"],
        ev(amounts["水・飲料"], float(TARGETS["水・飲料"]), "L") + (f"\n設備能力: {len(water_capacity)}件（別枠）" if water_capacity else ""),
    )

    # 7-1 食
    card(
        "7-1(食)",
        "避難想定人数に対する主食類の備蓄",
        amounts["主食類"] >= TARGETS["主食類"],
        ev(amounts["主食類"], float(TARGETS["主食類"]), "食"),
    )

    # 6-5 トイレ（内訳表示）
    need_toilet = float(TARGETS["トイレ・衛生"])
    have_toilet = float(amounts["トイレ・衛生"])

    # 内訳
    by_sub: Dict[str, Dict[str, float]] = {}
    for s in stocks:
        if get_cat_key(s.get("category")) != "トイレ・衛生":
            continue
        if str(s.get("item_kind") or "stock").lower() != "stock":
            continue
        sub = str(s.get("subtype") or "").strip() or infer_toilet_subtype(str(s.get("name") or ""))
        if sub not in TOILET_SUBTYPES:
            sub = "その他"
        unit = str(s.get("unit") or "").strip() or _default_unit_for("トイレ・衛生", "stock", sub)
        qty = float(s.get("qty") or 0)

        if sub not in by_sub:
            by_sub[sub] = {"回": 0.0, "基": 0.0}
        uses = toilet_uses_from_unit(qty, unit)
        if uses is not None:
            by_sub[sub]["回"] += uses
        else:
            by_sub[sub]["基"] += qty

    parts = []
    for sub in TOILET_SUBTYPES:
        if sub not in by_sub:
            continue
        if by_sub[sub]["回"] > 0:
            parts.append(f"- {sub}: {fmt_qty(by_sub[sub]['回'])}回")
        if by_sub[sub]["基"] > 0:
            parts.append(f"- {sub}: {fmt_qty(by_sub[sub]['基'])}基")
    breakdown = "\n".join(parts) if parts else "(内訳データなし)"

    card(
        "6-5",
        "簡易トイレ等の物資の備え（回換算 + 種類別内訳）",
        have_toilet >= need_toilet,
        ev(have_toilet, need_toilet, "回") + f"\n内訳:\n{breakdown}\n※「基」は回換算できないため別枠表示（今後ルール追加可）",
    )

    # 7-2 乳幼児
    card(
        "7-2",
        "乳幼児・要配慮者への備え",
        amounts["乳幼児用品"] > 0,
        f"該当カテゴリ在庫 {fmt_qty(amounts['乳幼児用品'])}点",
    )

def page_inventory() -> None:
    back_to_home("inv_back")

    # ---- category select
    if st.session_state.inv_cat is None:
        st.markdown("## 📦 備蓄・登録（カテゴリ選択）")
        cols = st.columns(2)
        for i, cat in enumerate(CATEGORIES):
            icon = CATEGORIES[cat]
            have = float(amounts.get(cat, 0))

            exp = soon30 = 0
            for s in stocks:
                if get_cat_key(s.get("category")) != cat:
                    continue
                dt = iso_to_date(str(s.get("due_date") or ""))
                if not dt:
                    continue
                if dt < today:
                    exp += 1
                elif dt <= today + timedelta(days=30):
                    soon30 += 1

            label = f"{icon}\n{cat}\n\n在庫: {fmt_qty(have)}"
            if cat == "水・飲料" and water_capacity:
                label += f"\n設備: {len(water_capacity)}件"
            if exp:
                label += f"\n期限切れ: {exp}"
            elif soon30:
                label += f"\n30日以内: {soon30}"

            with cols[i % 2]:
                if st.button(label, key=f"cat_{cat}", type="primary"):
                    st.session_state.inv_cat = cat
                    st.rerun()

        if st.session_state.pending_items:
            st.markdown("---")
            st.info(f"🧺 未登録カート: {len(st.session_state.pending_items)}件（カテゴリを開いて登録できます）")
        return

    # ---- category detail
    cat = st.session_state.inv_cat
    cat_key = get_cat_key(cat)
    st.markdown(f"## {CATEGORIES[cat_key]} {cat_key}")

    if st.button("🔙 カテゴリ選択に戻る", key="inv_back_cat", type="secondary"):
        st.session_state.inv_cat = None
        st.rerun()

    tab_add, tab_cart, tab_list = st.tabs(["➕ 追加（AI/手入力）", "🧺 未登録カート", "📦 在庫一覧"])

    # ---- Add tab
    with tab_add:
        st.markdown("### 📸 写真で追加（AI → カート）")
        if genai is None:
            st.warning("AI解析を使うには `google-generativeai` が必要です。")
        if not GEMINI_API_KEY:
            st.info("AI解析を使うには GEMINI_API_KEY が必要です（環境変数または .env）。")

        cam = st.camera_input("カメラで撮影（任意）", key=f"cam_{cat_key}")
        uploads = st.file_uploader("または画像を選択（複数OK）", type=["jpg", "jpeg", "png"], accept_multiple_files=True, key=f"upl_{cat_key}")
        imgs = []
        if cam is not None:
            imgs.append(cam)
        if uploads:
            imgs.extend(list(uploads))

        if st.button("🤖 AI解析 → カートに追加", key=f"ai_{cat_key}", type="secondary", disabled=(not imgs) or (not GEMINI_API_KEY) or (genai is None)):
            raw_all = []
            total = 0
            failed = []
            for i, img in enumerate(imgs):
                with st.spinner(f"AI解析中... ({i+1}/{len(imgs)})"):
                    try:
                        items, raw = gemini_extract_from_image(img, cat_key)
                        raw_all.append(raw)
                        for it in items:
                            it["category"] = cat_key
                            cart_add(it)
                        total += len(items)
                    except Exception as e:
                        failed.append(str(e))
            st.session_state.ai_last_raw = "\n\n---\n\n".join(raw_all)
            if failed:
                st.error("AI解析に失敗した画像があります:\n- " + "\n- ".join(failed))
            if total:
                toast(f"カートに追加: {total}件", icon="🧺")
            else:
                st.warning("抽出結果が空でした。写真が暗い/ブレている場合は別角度でもう1枚撮ってください。")
            st.rerun()

        with st.expander("AI 生ログ（デバッグ用）", expanded=False):
            st.code(st.session_state.ai_last_raw or "", language="text")

        st.markdown("---")
        st.markdown("### ✍️ 手入力で追加（カートへ）")

        recent = db.get_recent_names(cat_key, limit=20)
        pick = st.selectbox("最近の品名（任意）", ["(選択なし)"] + recent, key=f"pick_{cat_key}")
        if pick and pick != "(選択なし)":
            st.session_state[f"manual_name_{cat_key}"] = pick

        ss_init(f"manual_name_{cat_key}", "")
        ss_init(f"manual_qty_{cat_key}", 1.0)
        ss_init(f"manual_unit_{cat_key}", _default_unit_for(cat_key, "stock", ""))
        ss_init(f"manual_kind_{cat_key}", "stock")
        ss_init(f"manual_sub_{cat_key}", "その他")
        ss_init(f"manual_memo_{cat_key}", "")
        ss_init(f"manual_due_type_{cat_key}", "none")

        # kind
        if cat_key == "水・飲料":
            kind = st.radio(
                "登録種別（飲料水）",
                options=["stock", "capacity"],
                horizontal=True,
                format_func=lambda x: ITEM_KIND_LABEL[x],
                key=f"manual_kind_{cat_key}",
            )
        else:
            kind = "stock"
            st.session_state[f"manual_kind_{cat_key}"] = "stock"

        name = st.text_input("品名", key=f"manual_name_{cat_key}", placeholder="例: 保存水 500ml×24本 / 造水機 / 携帯トイレ 100回分")
        qty = st.number_input("数量", min_value=0.0, step=1.0, key=f"manual_qty_{cat_key}")

        # unit + subtype
        if cat_key == "水・飲料" and kind == "stock":
            unit = st.selectbox("入力単位（自動でL換算して保存）", ["L", "ml", "m3", "本", "ケース"], key=f"manual_unit_{cat_key}")
            subtype = ""
            st.caption("例: '500ml×24本' で unit=本, qty=24 → 12L に自動換算されます。")
        elif cat_key == "水・飲料" and kind == "capacity":
            unit = st.text_input("単位（設備能力）", value="台", key=f"manual_unit_{cat_key}")
            subtype = ""
            st.caption("設備能力はスコアに合算しません（在庫と別表示）。")
        elif cat_key == "トイレ・衛生":
            subtype = st.selectbox("種類", TOILET_SUBTYPES, key=f"manual_sub_{cat_key}")
            # 組立/仮設は「基」、それ以外は「回」を推奨
            default_unit = "基" if subtype in {"組立トイレ", "仮設トイレ"} else "回"
            unit = st.selectbox("単位", ["回", "基"], index=0 if default_unit == "回" else 1, key=f"manual_unit_{cat_key}")
        elif cat_key == "主食類":
            unit = st.selectbox("入力単位（自動で食換算して保存）", ["食", "箱", "袋", "ケース"], key=f"manual_unit_{cat_key}")
            subtype = ""
            st.caption("箱/袋/ケースの場合、品名に '50食' のように食数を含めると自動換算します。")
        else:
            unit = st.text_input("単位（任意）", value=str(st.session_state.get(f"manual_unit_{cat_key}") or "点"), key=f"manual_unit_{cat_key}")
            subtype = ""

        due_type, due_date = render_due_inputs(f"manual_{cat_key}", st.session_state[f"manual_due_type_{cat_key}"], "")
        st.session_state[f"manual_due_type_{cat_key}"] = due_type
        memo = st.text_area("メモ（任意）", key=f"manual_memo_{cat_key}", height=80)

        if st.button("🧺 カートに追加", key=f"manual_add_{cat_key}", type="secondary", disabled=not bool(name.strip())):
            cart_add(
                {
                    "name": name,
                    "qty": float(qty),
                    "unit": unit,
                    "category": cat_key,
                    "item_kind": kind,
                    "subtype": subtype,
                    "due_type": due_type,
                    "due_date": due_date,
                    "memo": memo,
                }
            )
            st.session_state[f"manual_name_{cat_key}"] = ""
            st.session_state[f"manual_qty_{cat_key}"] = 1.0
            st.session_state[f"manual_memo_{cat_key}"] = ""
            toast("カートに追加しました", icon="🧺")
            st.rerun()

    # ---- Cart tab
    with tab_cart:
        st.markdown("### 🧺 未登録カート（登録前にここで修正）")

        scope = st.radio("表示範囲", ["このカテゴリ", "全カテゴリ"], horizontal=True, key="cart_view_scope")
        all_items: List[Dict[str, Any]] = list(st.session_state.pending_items)
        view = [it for it in all_items if scope == "全カテゴリ" or get_cat_key(it.get("category")) == cat_key]

        if not all_items:
            st.info("カートは空です。")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("↩️ Undo", key=f"undo_{cat_key}", type="secondary", disabled=not bool(st.session_state.undo_stack)):
                    cart_undo()
                    toast("戻しました", icon="↩️")
                    st.rerun()
            with c2:
                if st.button("🧩 同じ品をまとめる", key=f"merge_{cat_key}", type="secondary"):
                    cart_merge_duplicates()
                    toast("整理しました", icon="🧩")
                    st.rerun()
            with c3:
                st.markdown(f"<span class='badge'>件数: {len(view)}</span>", unsafe_allow_html=True)

            for it in view:
                item_id = it["id"]
                prefix = f"p_{item_id}"

                ss_init(f"{prefix}_name", it.get("name", ""))
                ss_init(f"{prefix}_qty", float(it.get("qty", 0) or 0))
                ss_init(f"{prefix}_memo", it.get("memo", ""))
                ss_init(f"{prefix}_kind", it.get("item_kind", "stock"))
                ss_init(f"{prefix}_unit", it.get("unit", ""))
                ss_init(f"{prefix}_sub", it.get("subtype", ""))
                ss_init(f"{prefix}_due_type", it.get("due_type", "none"))
                ss_init(f"{prefix}_due_date", iso_to_date(it.get("due_date", "")) or today)

                badge, dk = due_badge(it.get("due_type"), it.get("due_date"), today)
                kind_icon = "🚨" if dk == "danger" else "⚠️" if dk == "warn" else "✅" if dk == "ok" else "➖"

                needs_fix = bool(it.get("needs_fix"))
                fix_mark = "🛠️" if needs_fix else ""
                cat_show = get_cat_key(it.get("category"))
                title = (
                    f"{fix_mark}{kind_icon} {CATEGORIES.get(cat_show,'📦')} {it.get('name','')} "
                    f"×{fmt_qty(float(it.get('qty',0) or 0))}{it.get('unit','')} "
                    f"| {ITEM_KIND_LABEL.get(str(it.get('item_kind') or 'stock'),'')} "
                    f"| {due_label(it.get('due_type'), it.get('due_date'))}  [{badge}]"
                )

                with st.expander(title, expanded=False):
                    if needs_fix:
                        st.error(f"この項目は登録前に修正が必要です: {it.get('fix_reason')}")

                    name2 = st.text_input("品名", key=f"{prefix}_name")

                    # kind
                    if cat_show == "水・飲料":
                        kind2 = st.radio(
                            "登録種別（飲料水）",
                            options=["stock", "capacity"],
                            horizontal=True,
                            format_func=lambda x: ITEM_KIND_LABEL[x],
                            key=f"{prefix}_kind",
                        )
                    else:
                        kind2 = "stock"
                        st.session_state[f"{prefix}_kind"] = "stock"

                    # subtype / unit / qty
                    if cat_show == "トイレ・衛生":
                        sub2 = st.selectbox("種類", TOILET_SUBTYPES, key=f"{prefix}_sub")
                        unit2 = st.selectbox("単位", ["回", "基"], key=f"{prefix}_unit")
                        step = 1.0
                        qty2 = st.number_input("数量", min_value=0.0, step=step, key=f"{prefix}_qty")
                    elif cat_show == "水・飲料" and kind2 == "stock":
                        st.caption("在庫（消耗品）は L で管理します。")
                        st.session_state[f"{prefix}_unit"] = "L"
                        step = 0.5
                        qty2 = st.number_input("数量（L）", min_value=0.0, step=step, key=f"{prefix}_qty")
                        unit2 = "L"
                        sub2 = ""
                    elif cat_show == "水・飲料" and kind2 == "capacity":
                        unit2 = st.text_input("単位（設備能力）", key=f"{prefix}_unit")
                        step = 1.0
                        qty2 = st.number_input("数量", min_value=0.0, step=step, key=f"{prefix}_qty")
                        sub2 = ""
                    elif cat_show == "主食類" and kind2 == "stock":
                        st.caption("主食類（在庫）は 食 で管理します。")
                        st.session_state[f"{prefix}_unit"] = "食"
                        step = 1.0
                        qty2 = st.number_input("数量（食）", min_value=0.0, step=step, key=f"{prefix}_qty")
                        unit2 = "食"
                        sub2 = ""
                    else:
                        unit2 = st.text_input("単位（任意）", key=f"{prefix}_unit")
                        step = 1.0
                        qty2 = st.number_input("数量", min_value=0.0, step=step, key=f"{prefix}_qty")
                        sub2 = ""

                    due_type2, due_date2 = render_due_inputs(prefix, st.session_state[f"{prefix}_due_type"], it.get("due_date", ""))
                    st.session_state[f"{prefix}_due_type"] = due_type2
                    memo2 = st.text_area("メモ", key=f"{prefix}_memo", height=80)

                    a1, a2 = st.columns(2)
                    with a1:
                        if st.button("🗑️ 削除", key=f"{prefix}_del", type="secondary"):
                            cart_remove(item_id)
                            toast("削除しました（Undo可）", icon="🗑️")
                            st.rerun()
                    with a2:
                        if st.button("📄 複製", key=f"{prefix}_dup", type="secondary"):
                            cart_duplicate(item_id)
                            toast("複製しました", icon="📄")
                            st.rerun()

                    # sync back
                    it["name"] = db.normalize_name(name2)
                    it["item_kind"] = kind2
                    it["subtype"] = sub2
                    it["unit"] = unit2
                    it["qty"] = float(qty2)
                    it["due_type"] = due_type2
                    it["due_date"] = due_date2 if due_type2 != "none" else ""
                    it["memo"] = str(memo2 or "").strip()

                    # 再canonicalize（編集によって壊れた場合を補正）
                    it2 = _canonicalize_cart_item(it)
                    it.update(it2)

            st.markdown("---")
            st.markdown("### ✅ まとめてDBへ登録")

            commit_scope = st.radio("登録範囲", ["このカテゴリだけ", "全カテゴリ"], horizontal=True, key=f"commit_scope_{cat_key}")
            to_commit = [it for it in st.session_state.pending_items if commit_scope == "全カテゴリ" or get_cat_key(it.get("category")) == cat_key]

            colA, colB = st.columns(2)
            with colA:
                if st.button("✅ DBへ登録する", key=f"commit_{cat_key}", type="secondary", disabled=not bool(to_commit)):
                    res = cart_commit(to_commit)
                    if res.get("errors"):
                        st.error("登録に失敗しました。修正が必要な項目があります。")
                        st.json(res["errors"][:20])
                        st.stop()
                    committed_ids = {it["id"] for it in to_commit}
                    st.session_state.pending_items = [it for it in st.session_state.pending_items if it.get("id") not in committed_ids]
                    toast(f"登録完了: 新規 {res.get('inserted',0)} / 合算 {res.get('merged',0)}", icon="✅")
                    st.rerun()

            with colB:
                ck = f"clear_cart_confirm_{cat_key}"
                ss_init(ck, False)
                st.checkbox("カートを空にする（確認）", key=ck)
                if st.button("🧹 カートを空にする", key=f"clear_cart_{cat_key}", type="secondary", disabled=not bool(st.session_state[ck])):
                    if commit_scope == "全カテゴリ":
                        st.session_state.pending_items = []
                        st.session_state.undo_stack = []
                    else:
                        st.session_state.pending_items = [it for it in st.session_state.pending_items if get_cat_key(it.get("category")) != cat_key]
                    toast("カートを空にしました", icon="🧹")
                    st.rerun()

    # ---- List tab
    with tab_list:
        st.markdown("### 📦 在庫一覧（DB）")
        rows = [s for s in stocks if get_cat_key(s.get("category")) == cat_key]
        if not rows:
            st.info("このカテゴリの在庫はまだ登録されていません。")
        else:
            q = st.text_input("検索（品名/メモ）", key=f"q_{cat_key}", placeholder="例: 水 / 棚A")
            sort = st.radio("並び順", ["期限が近い順", "品名順"], horizontal=True, key=f"sort_{cat_key}")

            def ok_row(s: Dict[str, Any]) -> bool:
                if not q:
                    return True
                text = f"{s.get('name','')} {s.get('memo','')}".lower()
                return q.lower() in text

            rows = [s for s in rows if ok_row(s)]
            if sort == "期限が近い順":
                rows.sort(
                    key=lambda s: (
                        iso_to_date(str(s.get("due_date") or "")) is None,
                        iso_to_date(str(s.get("due_date") or "")) or date(9999, 12, 31),
                        db.normalize_name(s.get("name", "")).lower(),
                    )
                )
            else:
                rows.sort(key=lambda s: db.normalize_name(s.get("name", "")).lower())

            st.caption(f"表示: {len(rows)}件")

            for s in rows:
                sid = int(s["id"])
                prefix = f"db_{sid}"
                ss_init(f"{prefix}_name", s.get("name", ""))
                ss_init(f"{prefix}_qty", float(s.get("qty", 0) or 0))
                ss_init(f"{prefix}_memo", s.get("memo", ""))
                ss_init(f"{prefix}_kind", str(s.get("item_kind") or "stock"))
                ss_init(f"{prefix}_unit", str(s.get("unit") or ""))
                ss_init(f"{prefix}_sub", str(s.get("subtype") or ""))
                ss_init(f"{prefix}_due_type", s.get("due_type", "none"))
                ss_init(f"{prefix}_due_date", iso_to_date(s.get("due_date", "")) or today)

                badge, dk = due_badge(s.get("due_type"), s.get("due_date"), today)
                kind_icon = "🚨" if dk == "danger" else "⚠️" if dk == "warn" else "✅" if dk == "ok" else "➖"
                title = f"{kind_icon} {s.get('name','')} ×{fmt_qty(float(s.get('qty') or 0))}{s.get('unit','')} | {ITEM_KIND_LABEL.get(str(s.get('item_kind') or 'stock'),'')} | {due_label(s.get('due_type'), s.get('due_date'))}  [{badge}]"

                with st.expander(title, expanded=False):
                    name2 = st.text_input("品名", key=f"{prefix}_name")

                    kind2 = st.selectbox(
                        "種別",
                        options=["stock", "capacity"],
                        format_func=lambda x: ITEM_KIND_LABEL[x],
                        key=f"{prefix}_kind",
                    )

                    if cat_key == "トイレ・衛生":
                        sub2 = st.selectbox("種類", TOILET_SUBTYPES, key=f"{prefix}_sub")
                        unit2 = st.selectbox("単位", ["回", "基"], key=f"{prefix}_unit")
                        qty2 = st.number_input("数量", min_value=0.0, step=1.0, key=f"{prefix}_qty")
                    elif cat_key == "水・飲料" and kind2 == "stock":
                        st.caption("在庫（消耗品）は L で管理します。")
                        st.session_state[f"{prefix}_unit"] = "L"
                        sub2 = ""
                        unit2 = "L"
                        qty2 = st.number_input("数量（L）", min_value=0.0, step=0.5, key=f"{prefix}_qty")
                    elif cat_key == "主食類" and kind2 == "stock":
                        st.caption("主食類（在庫）は 食 で管理します。")
                        st.session_state[f"{prefix}_unit"] = "食"
                        sub2 = ""
                        unit2 = "食"
                        qty2 = st.number_input("数量（食）", min_value=0.0, step=1.0, key=f"{prefix}_qty")
                    else:
                        sub2 = st.text_input("分類（任意）", key=f"{prefix}_sub")
                        unit2 = st.text_input("単位（任意）", key=f"{prefix}_unit")
                        qty2 = st.number_input("数量", min_value=0.0, step=1.0, key=f"{prefix}_qty")

                    due_type2, due_date2 = render_due_inputs(prefix, st.session_state[f"{prefix}_due_type"], s.get("due_date", ""))
                    st.session_state[f"{prefix}_due_type"] = due_type2
                    memo2 = st.text_area("メモ", key=f"{prefix}_memo", height=80)

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("💾 更新", key=f"{prefix}_save", type="secondary"):
                            try:
                                res = db.update_stock(
                                    sid,
                                    name=name2,
                                    qty=float(qty2),
                                    unit=unit2,
                                    category=cat_key,
                                    item_kind=kind2,
                                    subtype=sub2,
                                    due_type=due_type2,
                                    due_date=due_date2,
                                    memo=memo2,
                                )
                                toast(f"更新しました（{res.get('action')}）", icon="💾")
                                st.rerun()
                            except Exception as e:
                                st.error(f"更新に失敗: {e}")
                    with c2:
                        dk2 = f"{prefix}_del_confirm"
                        ss_init(dk2, False)
                        st.checkbox("削除する（確認）", key=dk2)
                        if st.button("🗑️ 削除", key=f"{prefix}_del", type="secondary", disabled=not bool(st.session_state[dk2])):
                            db.delete_stock(sid)
                            toast("削除しました", icon="🗑️")
                            st.rerun()

                    st.caption(f"最終更新: {s.get('updated_at','')}")

def page_data() -> None:
    back_to_home("data_back")
    st.markdown("## 💾 データ管理")

    df = pd.DataFrame(stocks)
    st.download_button(
        "📥 CSVダウンロード（バックアップ）",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"bousai_backup_{datetime.now().strftime('%Y%m%d')}.csv",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("### 📤 CSV取り込み（追加/統合）")
    up = st.file_uploader("CSVファイル", type=["csv"], key="csv_up")
    if up is not None:
        try:
            df_in = pd.read_csv(up)
        except Exception:
            up.seek(0)
            df_in = pd.read_csv(up, encoding="utf-8-sig")

        st.dataframe(df_in.head(50), use_container_width=True)
        mode = st.radio("取り込み方法", ["追加/統合（推奨）", "全件置換（危険）"], horizontal=True, key="import_mode")
        confirm = st.checkbox("取り込みを実行する（確認）", key="import_ok")

        if st.button("✅ 取り込み実行", key="import_go", type="secondary", disabled=not confirm):
            cols = {c.lower(): c for c in df_in.columns}

            def col(*names: str) -> Optional[str]:
                for n in names:
                    if n in cols:
                        return cols[n]
                return None

            name_col = col("name", "item")
            qty_col = col("qty", "quantity")
            unit_col = col("unit")
            kind_col = col("item_kind", "kind")
            sub_col = col("subtype", "type")
            cat_col = col("category", "cat")
            due_type_col = col("due_type", "duetype")
            due_date_col = col("due_date", "duedate", "expiry_date")
            memo_col = col("memo", "note", "notes")

            if not name_col or not qty_col or not cat_col:
                st.error("CSVに必要な列がありません。最低限: name(or item), qty, category")
                st.stop()

            items: List[Dict[str, Any]] = []
            for _, r in df_in.iterrows():
                name = str(r.get(name_col, "")).strip()
                if not name:
                    continue
                try:
                    qty = float(r.get(qty_col, 0) or 0)
                except Exception:
                    qty = 0.0
                category = str(r.get(cat_col, "その他")).strip() or "その他"
                unit = str(r.get(unit_col, "") if unit_col else "").strip()
                item_kind = str(r.get(kind_col, "stock") if kind_col else "stock").strip().lower()
                subtype = str(r.get(sub_col, "") if sub_col else "").strip()

                due_type = str(r.get(due_type_col, "none") if due_type_col else "none").strip().lower()
                due_date_raw = str(r.get(due_date_col, "") if due_date_col else "").strip()
                dd = iso_to_date(due_date_raw)
                due_date = dd.isoformat() if dd else ""
                memo = str(r.get(memo_col, "") if memo_col else "").strip()

                items.append(
                    {
                        "name": name,
                        "qty": qty,
                        "unit": unit,
                        "category": category,
                        "item_kind": item_kind,
                        "subtype": subtype,
                        "due_type": due_type,
                        "due_date": due_date,
                        "memo": memo,
                    }
                )

            if mode.startswith("全件置換"):
                db.clear_all()

            # カート経由ではないので、最低限の正規化を通す
            normalized = [_canonicalize_cart_item({**it, "id": uuid.uuid4().hex}) for it in items]
            payload, errors = validate_items_for_commit(normalized)
            if errors:
                st.error("CSV取り込みに失敗（修正が必要）:")
                st.json(errors[:30])
                st.stop()

            res = db.bulk_upsert(payload, atomic=True)
            if res.get("errors"):
                st.error("取り込みに失敗しました。")
                st.json(res["errors"][:20])
                st.stop()

            toast(f"取り込み完了: 新規 {res.get('inserted',0)} / 合算 {res.get('merged',0)}", icon="✅")
            st.rerun()

    st.markdown("---")
    st.markdown("### 💥 全データ削除（危険）")
    confirm = st.checkbox("本当に全データを削除する（確認）", key="wipe_ok")
    if st.button("🧨 全データ削除", key="wipe_go", type="secondary", disabled=not confirm):
        db.clear_all()
        toast("削除しました", icon="🧨")
        st.rerun()

# =========================
# Router
# =========================
if st.session_state.current_page == "home":
    page_home()
elif st.session_state.current_page == "dashboard":
    page_dashboard()
elif st.session_state.current_page == "inspection":
    page_inspection()
elif st.session_state.current_page == "inventory":
    page_inventory()
elif st.session_state.current_page == "data":
    page_data()
else:
    st.session_state.current_page = "home"
    st.rerun()
