import os
import re
import json
import ast
import time
import inspect
import uuid
from datetime import datetime, date
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

try:
    import google.generativeai as genai  # legacy SDK
except Exception:
    genai = None

import db


APP_TITLE = "香川防災DX"

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="collapsed",
)


# -------------------------
# Session
# -------------------------
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default


ss_init("current_page", "home")
ss_init("inv_cat", None)
ss_init("pending_items", [])  # AI→カート
ss_init("api_key", "")
ss_init("model_name", "")
ss_init("use_rest_transport", True)


def navigate_to(page: str) -> None:
    st.session_state.current_page = page
    st.rerun()


# -------------------------
# UI helper
# -------------------------
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters


def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    if _SUPPORTS_WIDTH:
        return st.button(label, key=key, type=type, width="stretch", **kwargs)
    return st.button(label, key=key, type=type, use_container_width=True, **kwargs)


# -------------------------
# Constants
# -------------------------
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

ITEM_KIND = ["stock", "capacity"]
ITEM_KIND_LABEL = {"stock": "在庫（消耗品）", "capacity": "設備能力（耐久財）"}

TOILET_SUBTYPES = ["", "携帯トイレ", "組立トイレ", "仮設トイレ", "トイレ袋", "凝固剤", "その他"]

# 2026時点の現行モデルへ（1.5 / pro-vision は外す）
DEFAULT_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


# -------------------------
# CSS（ノッチ + ボタン反応 + 見た目）
# -------------------------
st.markdown(
    """
<style>
html { -webkit-text-size-adjust: 100%; }
.stApp { background-color: #f8fafc; }

/* ノッチ対策：固定値 + safe-area */
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

/* タップ不能対策：ボタンを最前面 */
div.stButton > button {
    position: relative !important;
    z-index: 50 !important;
    -webkit-tap-highlight-color: transparent;
}

/* タイル（tile_） */
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

/* 戻る（back_） */
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

/* カード */
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


# -------------------------
# Helpers
# -------------------------
def get_cat_key(cat: Any) -> str:
    s = str(cat or "")
    for k in CATEGORIES.keys():
        if k in s:
            return k
    return "その他"


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
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except Exception:
            return ""
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
        return None, "JSONは解析できたが配列/オブジェクトではありません"
    except Exception as e1:
        err1 = str(e1)

    chunk = extract_json_array(t) or ""
    if chunk:
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                obj = [obj]
            if isinstance(obj, list):
                return obj, ""
            return None, "抽出JSONは解析できたが配列/オブジェクトではありません"
        except Exception as e2:
            err2 = str(e2)
    else:
        err2 = "配列 [] が見つかりません"

    try:
        lit_src = chunk or t
        lit_src = lit_src.replace("null", "None").replace("true", "True").replace("false", "False")
        obj = ast.literal_eval(lit_src)
        if isinstance(obj, dict):
            obj = [obj]
        if isinstance(obj, list):
            norm = [x for x in obj if isinstance(x, dict)]
            return norm, ""
        return None, "literal_eval は成功したが list/dict ではありません"
    except Exception as e3:
        err3 = str(e3)

    return None, f"json.loads失敗: {err1} / 抽出json失敗: {err2} / literal_eval失敗: {err3}"


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

    if category == "トイレ・衛生":
        if subtype not in TOILET_SUBTYPES:
            subtype = "その他" if subtype else ""
    else:
        subtype = ""

    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "qty": qty,
        "unit": unit,
        "category": category,
        "item_kind": "stock",  # AIに任せない（カートで変更）
        "subtype": subtype,
        "due_type": due_type,
        "due_date": due_date,
        "memo": memo,
    }


# -------------------------
# Sidebar
# -------------------------
with st.sidebar:
    st.header("🔑 APIキー設定")
    api_key = st.text_input(
        "Google AI StudioのAPIキー",
        type="password",
        placeholder="AIzaSy...",
        value=st.session_state.get("api_key", ""),
    ).strip()
    st.session_state.api_key = api_key
    if not api_key:
        st.warning("👆 ここにAPIキーを入力してください（AI登録が有効になります）")

    st.markdown("---")
    st.header("🤖 AIモデル")
    model_default = st.session_state.get("model_name") or DEFAULT_MODELS[0]
    model_name = st.selectbox(
        "使用モデル",
        DEFAULT_MODELS,
        index=DEFAULT_MODELS.index(model_default) if model_default in DEFAULT_MODELS else 0,
    )
    st.session_state.model_name = model_name

    use_rest = st.toggle("通信方式をRESTに固定（推奨）", value=bool(st.session_state.get("use_rest_transport", True)))
    st.session_state.use_rest_transport = use_rest

    st.markdown("---")
    st.header("⚙️ 備蓄設定")
    t_pop = st.number_input("想定人数", 1, 1_000_000, 100, 100)
    t_days = st.slider("目標日数", 1, 7, 3)
    st.caption("※ AIがグルグルする場合はREST固定＋timeoutが効きます")


TARGETS = {
    "水・飲料": t_pop * 3 * t_days,
    "主食類": t_pop * 3 * t_days,
    "トイレ・衛生": t_pop * 5 * t_days,
}


# -------------------------
# DB & aggregation
# -------------------------
db.init_db()
stocks = db.get_all_stocks() or []
today = datetime.now().date()

amounts = {k: 0.0 for k in CATEGORIES}
expired_count = 0

for s in stocks:
    cat = get_cat_key(s.get("category"))
    qty = safe_float(s.get("qty", 0), default=0.0)
    unit = str(s.get("unit") or "").strip()

    if str(s.get("item_kind") or "stock") == "capacity":
        continue

    if cat == "トイレ・衛生":
        if unit in ["回", "枚", "袋", ""]:
            amounts[cat] += qty
    else:
        amounts[cat] += qty

    try:
        dd = str(s.get("due_date") or "").split("T")[0]
        if dd and date.fromisoformat(dd) < today:
            expired_count += 1
    except Exception:
        pass


# -------------------------
# Gemini (legacy) stable wrapper
# -------------------------
@st.cache_resource(show_spinner=False)
def _get_model(api_key: str, model_name: str, use_rest_transport: bool):
    if genai is None:
        raise RuntimeError("google-generativeai がインストールされていません（requirements を確認）")

    # gRPC詰まり対策
    if use_rest_transport:
        genai.configure(api_key=api_key, transport="rest")
    else:
        genai.configure(api_key=api_key)

    generation_config = {
        "temperature": 0.0,
        "max_output_tokens": 1024,
        "response_mime_type": "application/json",  # JSONモード
    }
    return genai.GenerativeModel(model_name, generation_config=generation_config)


def gemini_extract_from_image(
    pil_img: Image.Image,
    category: str,
    api_key: str,
    model_name: str,
    use_rest_transport: bool,
) -> Tuple[List[Dict[str, Any]], str]:
    if genai is None:
        return [], "google-generativeai が import できません（インストールされていない可能性）"
    if not api_key:
        return [], "サイドバーでAPIキーを入力してください"

    img = pil_img.convert("RGB")
    if max(img.size) > 1024:
        img.thumbnail((1024, 1024))

    try:
        model = _get_model(api_key, model_name, use_rest_transport)

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

        resp = model.generate_content([prompt, img], request_options={"timeout": 60})
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
            return [], f"AI出力は取れたが登録候補が0件（品名空など）\nRAW:\n{raw_text}"

        return out, raw_text

    except Exception as e:
        return [], str(e)


# -------------------------
# UI building blocks
# -------------------------
def back_home(key_suffix: str) -> None:
    if button_stretch("🔙 ホームに戻る", key=f"back_{key_suffix}", type="secondary"):
        st.session_state.inv_cat = None
        navigate_to("home")


def render_card(title: str, ok: bool, html_body: str) -> None:
    cls = "card-ok" if ok else "card-ng"
    icon = "🟢 適合" if ok else "🔴 不適合"
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


# -------------------------
# Pages
# -------------------------
def page_home() -> None:
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown("<div style='text-align:center; color:#64748b; margin-bottom:18px;'>物資DX台帳 × 自主点検</div>", unsafe_allow_html=True)

    if not st.session_state.api_key:
        st.info("👈 左上の「＞」からサイドバーを開き、APIキーを入力するとAI登録が有効になります。")

    c1, c2 = st.columns(2)
    with c1:
        if button_stretch("📊\n分析レポート\n(充足率)", key="tile_dash", type="primary"):
            navigate_to("dashboard")
        if button_stretch("✅\n自動自主点検\n(裏取り)", key="tile_insp", type="primary"):
            navigate_to("inspection")
    with c2:
        if button_stretch("📦\n備蓄・登録\n(AI→カート)", key="tile_inv", type="primary"):
            navigate_to("inventory")
        if button_stretch("💾\nデータ管理\n(CSV)", key="tile_data", type="primary"):
            navigate_to("data")

    st.markdown("---")
    if expired_count:
        st.error(f"🚨 期限切れ: {expired_count}件")
    else:
        st.success("✅ 期限切れなし")


def page_dashboard() -> None:
    back_home("dash")
    st.markdown("## 📊 充足率")
    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        denom = TARGETS[k] if TARGETS[k] > 0 else 1
        pct = min(amounts[k] / denom, 1.0)
        st.write(f"**{CATEGORIES[k]} {k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k]):,} / 目標: {int(TARGETS[k]):,}")


def page_inspection() -> None:
    back_home("insp")
    st.markdown("## ✅ 自動点検")

    with st.expander("🏢 施設情報（任意）", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 1000, 0, key="f_toilets")

    p_uses = amounts["トイレ・衛生"]
    units = float(f_toilets) + sum(
        safe_float(s.get("qty", 0), 0.0)
        for s in stocks
        if get_cat_key(s.get("category")) == "トイレ・衛生" and str(s.get("subtype") or "") in ["仮設トイレ", "組立トイレ"]
    )

    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days)
    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20

    ok_65 = (p_uses >= need_uses) and (units >= need_units)
    render_card(
        "6-5 簡易トイレ等の備え",
        ok_65,
        f"携帯: {int(p_uses):,}回 / 必要: {int(need_uses):,}回<br>基数: {int(units):,}基 / 必要: {int(need_units):,}基",
    )

    ok_71 = amounts["水・飲料"] >= TARGETS["水・飲料"]
    render_card(
        "7-1 飲料水の備え",
        ok_71,
        f"水: {int(amounts['水・飲料']):,} / 目標: {int(TARGETS['水・飲料']):,}",
    )


def _cart_editor(category: str) -> None:
    pending: List[Dict[str, Any]] = st.session_state.pending_items or []
    pending_cat = [p for p in pending if p.get("category") == category]

    if not pending_cat:
        st.info("🛒 まだカートに入っていません（AI解析するとここに出ます）")
        return

    st.markdown("### 🛒 カート（登録前に修正できます）")
    to_delete_ids: List[str] = []

    for p in pending_cat:
        pid = p.get("id") or uuid.uuid4().hex
        p["id"] = pid

        title = f"{p.get('name','(no name)')}  ×{int(p.get('qty',1))}"
        with st.expander(title, expanded=True):
            p["name"] = st.text_input("品名", value=str(p.get("name", "")), key=f"cart_name_{pid}")
            p["qty"] = st.number_input("数量", min_value=0.0, value=float(p.get("qty", 1.0)), step=1.0, key=f"cart_qty_{pid}")
            p["unit"] = st.text_input("単位", value=str(p.get("unit", "")), key=f"cart_unit_{pid}")
            p["memo"] = st.text_input("メモ", value=str(p.get("memo", "")), key=f"cart_memo_{pid}")

            if category == "水・飲料":
                kind = st.selectbox(
                    "種別（飲料水の二重計上防止）",
                    ITEM_KIND,
                    index=ITEM_KIND.index(p.get("item_kind", "stock")) if p.get("item_kind", "stock") in ITEM_KIND else 0,
                    format_func=lambda x: ITEM_KIND_LABEL.get(x, x),
                    key=f"cart_kind_{pid}",
                )
                p["item_kind"] = kind

            if category == "トイレ・衛生":
                stype = st.selectbox(
                    "種別（トイレ内訳）",
                    TOILET_SUBTYPES,
                    index=TOILET_SUBTYPES.index(p.get("subtype", "")) if p.get("subtype", "") in TOILET_SUBTYPES else 0,
                    key=f"cart_subtype_{pid}",
                )
                p["subtype"] = stype

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

            if st.button("🗑️ この行をカートから削除", key=f"cart_del_{pid}"):
                to_delete_ids.append(pid)

    if to_delete_ids:
        st.session_state.pending_items = [x for x in st.session_state.pending_items if x.get("id") not in to_delete_ids]
        st.rerun()

    st.markdown("---")
    if st.button("✅ このカテゴリのカートをDBへ登録", key=f"cart_commit_{category}", type="primary", use_container_width=True):
        commit_items = []
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
                    "unit": str(p.get("unit") or ""),
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
    st.markdown("## 📦 備蓄・登録（AI→カート→登録）")

    if st.session_state.inv_cat is None:
        st.markdown("### カテゴリ選択")
        cols = st.columns(2)
        for i, (cat, icon) in enumerate(CATEGORIES.items()):
            with cols[i % 2]:
                label = f"{icon}\n{cat}\n{int(amounts[cat]):,}"
                if button_stretch(label, key=f"tile_cat_{cat}", type="primary"):
                    st.session_state.inv_cat = cat
                    st.rerun()
        return

    cat = st.session_state.inv_cat
    st.markdown(f"## {CATEGORIES[cat]} {cat}")

    if button_stretch("🔙 カテゴリ一覧に戻る", key="back_cat", type="secondary"):
        st.session_state.inv_cat = None
        st.rerun()

    tab_ai, tab_cart, tab_list = st.tabs(["📸 AI解析", "🛒 カート編集", "📝 DB在庫リスト"])

    with tab_ai:
        st.markdown("### 📸 写真から抽出（まずカートに入ります）")

        colA, colB = st.columns(2)
        with colA:
            img_file = st.camera_input("撮影")
        with colB:
            img_file2 = st.file_uploader("または画像アップロード", type=["jpg", "jpeg", "png"])
            if img_file2 is not None:
                img_file = img_file2

        if genai is None:
            st.error("google-generativeai がインストールされていません。requirements.txt を確認してください。")
            return

        if img_file is not None:
            try:
                pil = Image.open(BytesIO(img_file.getvalue()))
                st.image(pil, caption="入力画像（縮小表示）", use_container_width=True)
            except Exception as e:
                st.error(f"画像の読み込みに失敗しました: {e}")
                pil = None
        else:
            pil = None

        if pil is None:
            st.info("画像を撮影/選択してください。")
            return

        if not st.session_state.api_key:
            st.warning("⚠️ サイドバーでAPIキーを入力してください。")
            return

        if st.button("解析してカートに追加", key="ai_run", type="primary", use_container_width=True):
            with st.spinner("AI解析中...（最大60秒でタイムアウトします）"):
                t0 = time.time()
                items, raw = gemini_extract_from_image(
                    pil,
                    cat,
                    st.session_state.api_key,
                    st.session_state.model_name,
                    st.session_state.use_rest_transport,
                )
                elapsed = time.time() - t0

            if not items:
                st.error(f"認識失敗 / 0件: {raw}")
            else:
                st.session_state.pending_items = (st.session_state.pending_items or []) + items
                st.success(f"カートに追加しました: {len(items)}件（{elapsed:.1f}s）")
                with st.expander("RAW（デバッグ用）"):
                    st.code(raw[:4000])
                st.rerun()

    with tab_cart:
        _cart_editor(cat)

    with tab_list:
        rows = [s for s in stocks if get_cat_key(s.get("category")) == cat]
        if not rows:
            st.info("このカテゴリの在庫はまだありません。")
        for s in rows:
            title = f"{s.get('name','')}（×{safe_float(s.get('qty',0),0):g}{s.get('unit','')}）"
            with st.expander(title):
                st.write(f"種別: {ITEM_KIND_LABEL.get(str(s.get('item_kind') or 'stock'), str(s.get('item_kind') or 'stock'))}")
                if s.get("subtype"):
                    st.write(f"種別(トイレ): {s.get('subtype')}")
                if s.get("due_date"):
                    st.write(f"期限: {s.get('due_type','none')} {s.get('due_date')}")
                if s.get("memo"):
                    st.write(f"メモ: {s.get('memo')}")
                if st.button("削除", key=f"del_{s.get('id')}"):
                    db.delete_stock(int(s.get("id")))
                    st.rerun()


def page_data() -> None:
    back_home("data")
    st.markdown("## 💾 データ管理")
    df = pd.DataFrame(stocks)

    st.download_button(
        "📥 CSV保存（utf-8-sig）",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"bousai_backup_{datetime.now().strftime('%Y%m%d')}.csv",
        use_container_width=True,
    )

    with st.expander("⚠️ 全データ削除（注意）"):
        if st.button("💥 全データ削除", key="clear_all", type="primary"):
            db.clear_all()
            st.success("削除しました")
            st.rerun()


# -------------------------
# Router
# -------------------------
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
