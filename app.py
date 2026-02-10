import inspect
import json
import random
import re
import time
import uuid
from datetime import date, datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

# Google AI (legacy SDK)
try:
    import google.generativeai as genai
except Exception:
    genai = None

import db


# =========================
# App config
# =========================
APP_TITLE = "香川防災DX"

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =========================
# Session state
# =========================
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default


ss_init("current_page", "home")
ss_init("inv_cat", None)

# AI cart
ss_init("pending_items", [])  # list[dict]

# AI settings
ss_init("api_key", "")
ss_init("model_name", "")
ss_init("use_rest_transport", True)

# paging state
ss_init("list_page", 0)
ss_init("list_page_size", 100)


def navigate_to(page: str) -> None:
    st.session_state.current_page = page
    st.rerun()


# =========================
# UI helper
# =========================
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters


def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    if _SUPPORTS_WIDTH:
        return st.button(label, key=key, type=type, width="stretch", **kwargs)
    return st.button(label, key=key, type=type, use_container_width=True, **kwargs)


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

DUE_TYPES = ["none", "expiry", "inspection"]
DUE_LABEL = {"none": "期限なし", "expiry": "賞味期限", "inspection": "点検日"}

ITEM_KINDS = ["stock", "capacity"]
ITEM_KIND_LABEL = {"stock": "在庫（消耗品）", "capacity": "設備能力（耐久財）"}

TOILET_SUBTYPES = ["", "携帯トイレ", "組立トイレ", "仮設トイレ", "トイレ袋", "凝固剤", "その他"]

DEFAULT_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


# =========================
# CSS (mobile safe-area + tappable buttons)
# =========================
st.markdown(
    """
<style>
html { -webkit-text-size-adjust: 100%; }
.stApp { background-color: #f8fafc; }

/* Notch safe-area: fixed padding + env() */
.block-container {
    max-width: 600px !important;
    margin: 0 auto !important;
    padding-top: calc(4.75rem + env(safe-area-inset-top)) !important;
    padding-bottom: calc(4.0rem + env(safe-area-inset-bottom)) !important;
    padding-left: 0.75rem !important;
    padding-right: 0.75rem !important;
}

h2 {
    text-align: center;
    font-weight: 900;
    color: #0f172a;
    margin-top: 0 !important;
    margin-bottom: 1.25rem !important;
}

/* Tap issues: ensure buttons are on top */
div.stButton > button {
    position: relative !important;
    z-index: 50 !important;
    -webkit-tap-highlight-color: transparent;
}

/* Tile buttons (key starts with tile_) */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button,
div.element-container[class*="st-key-tile_"] div.stButton > button {
    width: 100% !important;
    height: auto !important;
    min-height: clamp(132px, 26vw, 190px) !important;
    padding: clamp(16px, 4.5vw, 26px) !important;

    border-radius: 20px !important;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
    box-shadow: 0 10px 24px rgba(15, 23, 42, 0.10) !important;

    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;

    color: #0f172a !important;
}

div.stElementContainer[class*="st-key-tile_"] div.stButton > button *,
div.element-container[class*="st-key-tile_"] div.stButton > button * {
    font-size: clamp(17px, 4.8vw, 22px) !important;
    font-weight: 800 !important;
    line-height: 1.35 !important;
    white-space: pre-line !important;
    text-align: center !important;
    color: #0f172a !important;
}

div.stElementContainer[class*="st-key-tile_"] div.stButton > button:active,
div.element-container[class*="st-key-tile_"] div.stButton > button:active {
    transform: scale(0.97) !important;
    background: #f1f5f9 !important;
}

/* Back buttons (key starts with back_) */
div.stElementContainer[class*="st-key-back_"] div.stButton > button,
div.element-container[class*="st-key-back_"] div.stButton > button {
    width: 100% !important;
    height: 54px !important;
    border-radius: 14px !important;
    background: #e2e8f0 !important;
    border: none !important;
    color: #475569 !important;
    font-weight: 800 !important;
    z-index: 60 !important;
}

/* Cards */
.card {
    background: white;
    padding: 1.1rem;
    border-radius: 16px;
    box-shadow: 0 4px 10px rgba(0,0,0,0.05);
    margin-bottom: 16px;
    border-left: 8px solid #cbd5e1;
}
.card-ok { border-left-color: #22c55e !important; }
.card-ng { border-left-color: #ef4444 !important; }

#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
</style>
""",
    unsafe_allow_html=True,
)


# =========================
# Helpers
# =========================
def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if not s:
            return default
        m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else default
    except Exception:
        return default


def fmt_qty(x: Any) -> str:
    v = safe_float(x, 0.0)
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}"


def parse_date_any(s: Any) -> str:
    if s is None:
        return ""
    ss = str(s).strip()
    if not ss:
        return ""
    try:
        return date.fromisoformat(ss.split("T")[0]).isoformat()
    except Exception:
        pass
    m = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", ss)
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except Exception:
        return ""


def strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def extract_json_array(text: str) -> Optional[str]:
    i = text.find("[")
    j = text.rfind("]")
    if i >= 0 and j > i:
        return text[i : j + 1]
    return None


def parse_json_list(text: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    t = strip_code_fences(text)

    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            obj = [obj]
        if isinstance(obj, list):
            return obj, ""
        return None, "JSONは解析できたが配列ではありません"
    except Exception as e1:
        err1 = str(e1)

    chunk = extract_json_array(t)
    if chunk:
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                obj = [obj]
            if isinstance(obj, list):
                return obj, ""
            return None, "抽出JSONは解析できたが配列ではありません"
        except Exception as e2:
            err2 = str(e2)
    else:
        err2 = "配列[]が見つかりません"

    return None, f"json.loads失敗: {err1} / 抽出json失敗: {err2}"


def normalize_ai_item(raw: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    name = str(raw.get("name") or raw.get("item") or "").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        return None

    qty = safe_float(raw.get("qty", 1), default=1.0)
    if qty <= 0:
        qty = 1.0

    unit = str(raw.get("unit") or "").strip()
    subtype = str(raw.get("subtype") or "").strip()
    memo = str(raw.get("memo") or "").strip()

    due_type = str(raw.get("due_type") or "none").strip().lower()
    if due_type not in DUE_TYPES:
        due_type = "none"
    due_date = parse_date_any(raw.get("due_date") or "")

    if category != "トイレ・衛生":
        subtype = ""

    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "qty": qty,
        "unit": unit,
        "category": category,
        "item_kind": "stock",
        "subtype": subtype,
        "due_type": due_type,
        "due_date": due_date,
        "memo": memo,
    }


def is_transient_ai_error(msg: str) -> bool:
    m = (msg or "").lower()
    patterns = [
        "429",
        "rate",
        "quota",
        "timeout",
        "deadline",
        "temporarily",
        "unavailable",
        "503",
        "500",
        "internal",
        "connection",
        "reset",
        "broken pipe",
    ]
    return any(p in m for p in patterns)


# =========================
# Gemini wrapper: timeout + retry + JSON mode
# =========================
def gemini_extract_from_image(
    pil_img: Image.Image,
    category: str,
    api_key: str,
    model_name: str,
    use_rest_transport: bool,
    timeout_sec: int = 45,
    max_retries: int = 2,
) -> Tuple[List[Dict[str, Any]], str]:
    if genai is None:
        return [], "google-generativeai が import できません（requirements を確認してください）"
    if not api_key:
        return [], "サイドバーでAPIキーを入力してください"

    img = pil_img.convert("RGB")
    if max(img.size) > 1024:
        img.thumbnail((1024, 1024))

    generation_config = {
        "temperature": 0.0,
        "max_output_tokens": 1024,
        "response_mime_type": "application/json",
    }

    prompt = f"""
あなたは倉庫在庫台帳の入力支援AIです。
画像から、防災備蓄品の「品名・数量・単位・期限」を抽出してください。

# 重要ルール
- 出力は **JSON配列のみ**。説明文やMarkdownは禁止。
- 必ずダブルクォートを使う（' は使わない）。
- qty は数値（不明なら 1）。
- due_date は YYYY-MM-DD。不明なら ""。
- due_type は "expiry" / "inspection" / "none" のいずれか。
- カテゴリは固定: "{category}"
- 読み取れない場合は [] を返す。

# 出力スキーマ（このキーだけ）
[
  {{
    "name": "品名",
    "qty": 1,
    "unit": "単位(L,本,食,回,箱,基など)",
    "subtype": "トイレの場合のみ（携帯トイレ/組立トイレ/仮設トイレ/トイレ袋/凝固剤/その他）それ以外は空文字",
    "due_type": "expiry|inspection|none",
    "due_date": "YYYY-MM-DD",
    "memo": "補足"
  }}
]
""".strip()

    last_err = ""
    raw_text = ""

    for attempt in range(max_retries + 1):
        try:
            # Configure each call (Streamlit multi-session safe)
            try:
                if use_rest_transport:
                    genai.configure(api_key=api_key, transport="rest")
                else:
                    genai.configure(api_key=api_key)
            except TypeError:
                genai.configure(api_key=api_key)

            model = genai.GenerativeModel(model_name, generation_config=generation_config)
            resp = model.generate_content(
                [prompt, img],
                request_options={"timeout": int(timeout_sec)},
            )
            raw_text = (getattr(resp, "text", "") or "").strip()

            parsed, perr = parse_json_list(raw_text)
            if parsed is None:
                return [], f"JSON解析に失敗: {perr}\n---\nRAW:\n{raw_text}"

            out: List[Dict[str, Any]] = []
            for it in parsed:
                if isinstance(it, dict):
                    norm = normalize_ai_item(it, category)
                    if norm:
                        out.append(norm)

            if not out:
                return [], f"AI出力は取れましたが登録候補が0件です（品名空など）\nRAW:\n{raw_text}"

            return out, raw_text

        except Exception as e:
            last_err = str(e)
            if attempt < max_retries and is_transient_ai_error(last_err):
                sleep_s = min(8.0, (2 ** attempt)) + random.random()
                time.sleep(sleep_s)
                continue
            return [], last_err

    return [], last_err or raw_text


# =========================
# DB init & sidebar
# =========================
db.init_db()

with st.sidebar:
    st.header("APIキー設定")
    api_key = st.text_input(
        "Google AI StudioのAPIキー",
        type="password",
        placeholder="AIzaSy...",
        value=st.session_state.get("api_key", ""),
    ).strip()
    st.session_state.api_key = api_key
    if not api_key:
        st.warning("ここにAPIキーを入力するとAI登録が有効になります。")

    st.markdown("---")
    st.header("AIモデル")
    model_default = st.session_state.get("model_name") or DEFAULT_MODELS[0]
    model_name = st.selectbox(
        "使用モデル",
        DEFAULT_MODELS,
        index=DEFAULT_MODELS.index(model_default) if model_default in DEFAULT_MODELS else 0,
    )
    st.session_state.model_name = model_name

    use_rest = st.toggle(
        "通信方式をRESTに固定（推奨）",
        value=bool(st.session_state.get("use_rest_transport", True)),
    )
    st.session_state.use_rest_transport = use_rest

    st.markdown("---")
    st.header("備蓄設定")
    t_pop = st.number_input("想定人数", 1, 1_000_000, 100, 100)
    t_days = st.slider("目標日数", 1, 7, 3)

TARGETS = {
    "水・飲料": t_pop * 3 * t_days,
    "主食類": t_pop * 3 * t_days,
    "トイレ・衛生": t_pop * 5 * t_days,
}

# SQL aggregation (fast)
cat_stats_all = db.get_category_stats(exclude_capacity=False)  # inventory/list counts
cat_stats_consume = db.get_category_stats(exclude_capacity=True)  # dashboard/inspection
expiry = db.get_expiry_stats()  # expired/within30/within90


# =========================
# UI building blocks
# =========================
def back_home(key_suffix: str) -> None:
    if button_stretch("ホームに戻る", key=f"back_{key_suffix}", type="secondary"):
        st.session_state.inv_cat = None
        st.session_state.current_page = "home"
        st.rerun()


def render_card(title: str, ok: bool, html_body: str) -> None:
    cls = "card-ok" if ok else "card-ng"
    icon = "適合" if ok else "不適合"
    st.markdown(
        f"""
<div class="card {cls}">
  <div style="font-weight:900; margin-bottom:6px;">{title}</div>
  <div style="font-weight:800; margin-bottom:6px;">{icon}</div>
  <div style="font-size:0.95rem; color:#334155;">{html_body}</div>
</div>
""",
        unsafe_allow_html=True,
    )


# =========================
# Pages
# =========================
def page_home() -> None:
    st.markdown(f"## {APP_TITLE}")
    st.markdown(
        "<div style='text-align:center; color:#64748b; margin-bottom:18px;'>物資DX台帳 × 自主点検</div>",
        unsafe_allow_html=True,
    )

    if not st.session_state.api_key:
        st.info("左上の「＞」からサイドバーを開き、APIキーを入力するとAI登録が有効になります。")

    c1, c2 = st.columns(2)
    with c1:
        if button_stretch("分析レポート\n(充足率)", key="tile_dash", type="primary"):
            navigate_to("dashboard")
        if button_stretch("自動自主点検\n(裏取り)", key="tile_insp", type="primary"):
            navigate_to("inspection")
    with c2:
        if button_stretch("備蓄・登録\n(AI→カート→登録)", key="tile_inv", type="primary"):
            navigate_to("inventory")
        if button_stretch("データ管理\n(CSV/DB)", key="tile_data", type="primary"):
            navigate_to("data")

    st.markdown("---")
    if expiry["expired"] > 0:
        st.error(f"期限切れ: {expiry['expired']}件")
    elif expiry["within30"] > 0:
        st.warning(f"30日以内に期限: {expiry['within30']}件")
    elif expiry["within90"] > 0:
        st.info(f"90日以内に期限: {expiry['within90']}件")
    else:
        st.success("期限切れ・期限接近はありません")


def page_dashboard() -> None:
    back_home("dash")
    st.markdown("## 充足率レポート")

    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        have = float(cat_stats_consume.get(k, {}).get("qty", 0.0))
        need = float(TARGETS.get(k, 0) or 0)
        denom = need if need > 0 else 1.0
        pct = min(have / denom, 1.0)

        st.write(f"**{CATEGORIES[k]} {k}**")
        st.progress(pct)
        st.caption(
            f"数量合計: {fmt_qty(have)} / 目標: {fmt_qty(need)}   "
            f"（行数: {int(cat_stats_consume.get(k, {}).get('rows', 0))}）"
        )


def page_inspection() -> None:
    back_home("insp")
    st.markdown("## 自動点検")

    with st.expander("施設情報（任意）", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 2000, 0, 1)

    toilet = db.toilet_stats()
    portable_uses = float(toilet.get("portable_uses", 0.0))
    units_by_subtype = toilet.get("units_by_subtype", {}) or {}

    units_stock = float(units_by_subtype.get("仮設トイレ", 0.0)) + float(units_by_subtype.get("組立トイレ", 0.0))
    units_total = float(f_toilets) + units_stock

    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days)
    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20

    ok_65 = (portable_uses >= need_uses) and (units_total >= need_units)
    render_card(
        "6-5 簡易トイレ等の備え",
        ok_65,
        f"携帯（回換算）: {fmt_qty(portable_uses)} / 必要: {fmt_qty(need_uses)}<br>"
        f"基数（既設+仮設+組立）: {fmt_qty(units_total)} / 必要: {fmt_qty(need_units)}<br>"
        f"内訳: 既設={fmt_qty(f_toilets)} / 仮設={fmt_qty(units_by_subtype.get('仮設トイレ',0))} / 組立={fmt_qty(units_by_subtype.get('組立トイレ',0))}",
    )

    have_w = float(cat_stats_consume.get("水・飲料", {}).get("qty", 0.0))
    ok_71 = have_w >= TARGETS["水・飲料"]
    render_card(
        "7-1 飲料水の備え",
        ok_71,
        f"水: {fmt_qty(have_w)} / 目標: {fmt_qty(TARGETS['水・飲料'])}",
    )


def cart_editor(category: str) -> None:
    pending: List[Dict[str, Any]] = st.session_state.pending_items or []
    items = [p for p in pending if p.get("category") == category]

    if not items:
        st.info("カートは空です（AI解析するとここに追加されます）。")
        return

    st.markdown("### カート編集")
    to_delete: List[str] = []

    for p in items:
        pid = p.get("id") or uuid.uuid4().hex
        p["id"] = pid

        title = f"{p.get('name','(no name)')}  ×{fmt_qty(p.get('qty',1))}"
        with st.expander(title, expanded=True):
            p["name"] = st.text_input("品名", value=str(p.get("name", "")), key=f"cart_name_{pid}")
            p["qty"] = st.number_input("数量", min_value=0.0, value=float(p.get("qty", 1.0)), step=1.0, key=f"cart_qty_{pid}")
            p["unit"] = st.text_input("単位", value=str(p.get("unit", "")), key=f"cart_unit_{pid}")
            p["memo"] = st.text_input("メモ", value=str(p.get("memo", "")), key=f"cart_memo_{pid}")

            kind = st.selectbox(
                "種別",
                ITEM_KINDS,
                index=ITEM_KINDS.index(p.get("item_kind", "stock")) if p.get("item_kind", "stock") in ITEM_KINDS else 0,
                format_func=lambda x: ITEM_KIND_LABEL.get(x, x),
                key=f"cart_kind_{pid}",
            )
            p["item_kind"] = kind

            if category == "トイレ・衛生":
                stype = st.selectbox(
                    "トイレ種別（内訳）",
                    TOILET_SUBTYPES,
                    index=TOILET_SUBTYPES.index(p.get("subtype", "")) if p.get("subtype", "") in TOILET_SUBTYPES else 0,
                    key=f"cart_subtype_{pid}",
                )
                p["subtype"] = stype
            else:
                p["subtype"] = ""

            due_type = st.selectbox(
                "期限種別",
                DUE_TYPES,
                index=DUE_TYPES.index(p.get("due_type", "none")) if p.get("due_type", "none") in DUE_TYPES else 0,
                format_func=lambda x: DUE_LABEL.get(x, x),
                key=f"cart_duetype_{pid}",
            )
            p["due_type"] = due_type

            if due_type != "none":
                current = parse_date_any(p.get("due_date"))
                default_d = date.fromisoformat(current) if current else date.today()
                date_key = f"cart_duedate_{pid}"
                if date_key not in st.session_state:
                    st.session_state[date_key] = default_d
                dval = st.date_input(DUE_LABEL[due_type], key=date_key)
                p["due_date"] = dval.isoformat() if isinstance(dval, date) else ""
            else:
                p["due_date"] = ""

            if st.button("この行をカートから削除", key=f"cart_del_{pid}"):
                to_delete.append(pid)

    if to_delete:
        st.session_state.pending_items = [x for x in (st.session_state.pending_items or []) if x.get("id") not in to_delete]
        st.rerun()

    st.markdown("---")
    if st.button("このカテゴリのカートをDBへ登録", key=f"cart_commit_{category}", type="primary", use_container_width=True):
        commit_items: List[Dict[str, Any]] = []
        for p in (st.session_state.pending_items or []):
            if p.get("category") != category:
                continue
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            commit_items.append(
                {
                    "name": name,
                    "qty": safe_float(p.get("qty", 0), 0.0),
                    "unit": str(p.get("unit") or "").strip(),
                    "category": category,
                    "item_kind": str(p.get("item_kind") or "stock"),
                    "subtype": str(p.get("subtype") or ""),
                    "due_type": str(p.get("due_type") or "none"),
                    "due_date": str(p.get("due_date") or ""),
                    "memo": str(p.get("memo") or ""),
                }
            )

        if not commit_items:
            st.warning("登録できるデータがありません（品名が空など）")
            return

        res = db.bulk_upsert(commit_items)
        st.session_state.pending_items = [x for x in (st.session_state.pending_items or []) if x.get("category") != category]
        st.success(f"登録しました（inserted={res.get('inserted',0)}, updated={res.get('updated',0)}）")
        st.rerun()


def page_inventory() -> None:
    back_home("inv")
    st.markdown("## 備蓄・登録（AI→カート→登録）")

    # Category selection
    if st.session_state.inv_cat is None:
        st.markdown("### カテゴリ選択（数量合計と行数を分けて表示）")
        cols = st.columns(2)
        for i, (cat, icon) in enumerate(CATEGORIES.items()):
            stat = cat_stats_all.get(cat, {"rows": 0, "qty": 0.0})
            label = f"{icon}\n{cat}\n数量: {fmt_qty(stat['qty'])}\n行: {int(stat['rows'])}"
            with cols[i % 2]:
                if button_stretch(label, key=f"tile_cat_{cat}", type="primary"):
                    st.session_state.inv_cat = cat
                    st.session_state.list_page = 0
                    st.rerun()
        return

    # Category detail
    cat = st.session_state.inv_cat
    st.markdown(f"## {CATEGORIES[cat]} {cat}")

    if button_stretch("カテゴリ一覧に戻る", key="back_cat", type="secondary"):
        st.session_state.inv_cat = None
        st.rerun()

    tab_ai, tab_cart, tab_list = st.tabs(["AI解析", "カート編集", "DB在庫リスト"])

    # --- AI tab (NO early returns, so other tabs always render) ---
    with tab_ai:
        st.markdown("### 写真から抽出（まずカートに入ります）")

        colA, colB = st.columns(2)
        with colA:
            img_file = st.camera_input("撮影")
        with colB:
            img_file2 = st.file_uploader("または画像アップロード", type=["jpg", "jpeg", "png"])
            if img_file2 is not None:
                img_file = img_file2

        pil: Optional[Image.Image] = None
        if img_file is not None:
            try:
                pil = Image.open(BytesIO(img_file.getvalue()))
                st.image(pil, caption="入力画像（縮小表示）", use_container_width=True)
            except Exception as e:
                st.error(f"画像の読み込みに失敗しました: {e}")
                pil = None
        else:
            st.info("画像を撮影/選択すると、ここでAI解析できます。")

        if not st.session_state.api_key:
            st.warning("サイドバーでAPIキーを入力してください（AI解析が有効になります）。")

        if pil is not None and st.session_state.api_key:
            if st.button("解析してカートに追加", key="ai_run", type="primary", use_container_width=True):
                with st.spinner("AI解析中...（タイムアウト+リトライあり）"):
                    t0 = time.time()
                    items, raw = gemini_extract_from_image(
                        pil,
                        cat,
                        st.session_state.api_key,
                        st.session_state.model_name,
                        st.session_state.use_rest_transport,
                        timeout_sec=45,
                        max_retries=2,
                    )
                    elapsed = time.time() - t0

                if not items:
                    st.error(f"認識失敗: {raw}")
                else:
                    st.session_state.pending_items = (st.session_state.pending_items or []) + items
                    st.success(f"カートに追加しました: {len(items)}件（{elapsed:.1f}秒）")
                    with st.expander("RAW（デバッグ用）"):
                        st.code(raw[:4000])
                    st.rerun()

    with tab_cart:
        cart_editor(cat)

    with tab_list:
        # Pagination
        total = db.count_by_category(cat)
        page_size = int(st.session_state.list_page_size)

        if total == 0:
            st.info("このカテゴリの在庫はまだありません。")
        else:
            max_page = max(0, (total - 1) // page_size)
            colp1, colp2 = st.columns([2, 1])
            with colp1:
                st.session_state.list_page = st.number_input(
                    "ページ",
                    min_value=0,
                    max_value=int(max_page),
                    value=int(min(st.session_state.list_page, max_page)),
                    step=1,
                )
            with colp2:
                st.session_state.list_page_size = st.selectbox(
                    "表示件数",
                    [50, 100, 200, 500],
                    index=[50, 100, 200, 500].index(page_size) if page_size in [50, 100, 200, 500] else 1,
                )

            offset = int(st.session_state.list_page) * int(st.session_state.list_page_size)
            rows = db.list_by_category(cat, limit=int(st.session_state.list_page_size), offset=offset)

            st.caption(f"全{total}行 / 表示 {offset+1}〜{min(offset+len(rows), total)} 行")

            for s in rows:
                title = f"{s.get('name','')}（×{fmt_qty(s.get('qty',0))}{s.get('unit','')}）"
                with st.expander(title):
                    st.write(f"種別: {ITEM_KIND_LABEL.get(str(s.get('item_kind') or 'stock'), str(s.get('item_kind') or 'stock'))}")
                    if s.get("subtype"):
                        st.write(f"トイレ種別: {s.get('subtype')}")
                    if s.get("due_type") and s.get("due_type") != "none":
                        st.write(f"期限: {DUE_LABEL.get(str(s.get('due_type')), str(s.get('due_type')))} {s.get('due_date')}")
                    if s.get("memo"):
                        st.write(f"メモ: {s.get('memo')}")
                    st.caption(f"id={s.get('id')} updated={s.get('updated_at')}")
                    if st.button("削除", key=f"del_{s.get('id')}"):
                        db.delete_stock(int(s.get("id")))
                        st.rerun()


def page_data() -> None:
    back_home("data")
    st.markdown("## データ管理")

    st.markdown("### CSVバックアップ")
    all_rows = db.export_all()
    df = pd.DataFrame(all_rows)
    st.download_button(
        "CSV保存（utf-8-sig）",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"bousai_backup_{datetime.now().strftime('%Y%m%d')}.csv",
        use_container_width=True,
    )

    st.markdown("### DBファイルバックアップ（stock.db）")
    try:
        with open(db.DB_PATH, "rb") as f:
            db_bytes = f.read()
        st.download_button(
            "DB保存（stock.db）",
            db_bytes,
            file_name="stock.db",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(f"DBファイルの読み込みに失敗しました: {e}")

    with st.expander("DB修復（重複/旧制約の解消）"):
        st.caption("旧DB由来のUNIQUE制約が残っている場合、ここで安全に再構築できます（データは legacy テーブルに退避されます）。")
        if st.button("DBを修復して再構築", type="primary"):
            with db.get_conn() as conn:
                legacy = db.rebuild_db(conn)
            st.success("再構築しました" + (f"（退避: {legacy}）" if legacy else ""))
            st.rerun()

    with st.expander("全データ削除（注意）"):
        if st.button("全データ削除", type="primary"):
            db.clear_all()
            st.success("削除しました")
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
