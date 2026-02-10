import os
import re
import json
import ast
import uuid
import inspect
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

try:
    import google.generativeai as genai
except Exception:
    genai = None  # Optional dependency

import db

# ============================================================
# App Config
# ============================================================
APP_TITLE = "香川防災DX"
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL", "gemini-1.5-flash") or "gemini-1.5-flash").strip()

# Read API key from env or .env (best-effort)
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
    initial_sidebar_state="collapsed",
)

# ============================================================
# UI Helper
# ============================================================
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters

def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    """Button that stretches full width across its container (Streamlit version compatible)."""
    if _SUPPORTS_WIDTH:
        return st.button(label, key=key, type=type, width="stretch", **kwargs)
    return st.button(label, key=key, type=type, use_container_width=True, **kwargs)

def navigate_to(page: str) -> None:
    st.session_state.current_page = page
    st.rerun()

# ============================================================
# Session State
# ============================================================
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss_init("current_page", "home")
ss_init("inv_cat", None)
ss_init("pending_items", [])
ss_init("undo_stack", [])
ss_init("ai_last_raw", "")

# ============================================================
# Constants
# ============================================================
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
TOILET_SUBTYPES = ["携帯トイレ", "組立トイレ", "仮設トイレ", "トイレ袋", "凝固剤", "その他"]

# ============================================================
# CSS: iPhone notch safe-area + tap/click fix + v5 design 유지
# ============================================================
st.markdown(
    """
<style>
/* -----------------------------
   iOS / Safari basics
--------------------------------*/
html { -webkit-text-size-adjust: 100%; }
* { box-sizing: border-box; }
.stApp { background-color: #f8fafc; }

/* -----------------------------
   Remove Streamlit top layers
   IMPORTANT: use display:none (not visibility:hidden) to avoid invisible overlays
--------------------------------*/
header[data-testid="stHeader"] { display: none !important; pointer-events: none !important; }
#stDecoration { display: none !important; pointer-events: none !important; }
div[data-testid="stDecoration"] { display: none !important; pointer-events: none !important; }
div[data-testid="stToolbar"] { display: none !important; pointer-events: none !important; }
div[data-testid="stStatusWidget"] { display: none !important; pointer-events: none !important; }
#MainMenu { display: none !important; }
footer { display: none !important; }

/* -----------------------------
   Safe area & layout
   FIX(最優先): notch対策として "固定値 + safe-area" で確実に押し下げる
--------------------------------*/
.block-container {
    max-width: 600px !important;
    margin: 0 auto !important;

    /* ここが最重要: 5rem + safe-area で確実に見切れ防止 */
    padding-top: calc(5rem + env(safe-area-inset-top, 0px)) !important;

    /* iPhone横向きも想定 */
    padding-left: calc(1rem + env(safe-area-inset-left, 0px)) !important;
    padding-right: calc(1rem + env(safe-area-inset-right, 0px)) !important;

    /* ホームインジケータ対策 */
    padding-bottom: calc(3.25rem + env(safe-area-inset-bottom, 0px)) !important;
}

/* Headings */
h1, h2, h3 {
    color: #0f172a !important;
    font-weight: 900 !important;
}
h2 {
    text-align: center !important;
    margin: 0 0 1.25rem 0 !important;
}

/* -----------------------------
   Clickability / z-index safety
   FIX: 透明要素の上被りでタップ不能になるのを防ぐ
--------------------------------*/
div[data-testid="stAppViewContainer"] { position: relative !important; z-index: 0 !important; }
section.main { position: relative !important; z-index: 0 !important; }
.block-container { position: relative !important; z-index: 1 !important; }

/* Buttons above everything (z-index効くように position も付与) */
div.stButton, div.stDownloadButton { position: relative !important; z-index: 100 !important; }
div.stButton > button, div.stDownloadButton > button {
    position: relative !important;
    z-index: 1000 !important;
    pointer-events: auto !important;
    -webkit-tap-highlight-color: rgba(0,0,0,0);
}

/* -----------------------------
   Tile buttons (key prefix: tile_)
   v5 design: white tiles + navy text
--------------------------------*/
div.stElementContainer[class*="st-key-tile_"] div.stButton > button,
div.element-container[class*="st-key-tile_"] div.stButton > button {
    width: 100% !important;

    height: auto !important;
    min-height: 155px !important;   /* FIX: 小さくなりすぎ防止 */

    padding: 20px 12px !important;

    border-radius: 18px !important;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
    box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08) !important;

    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    align-items: center !important;
    text-align: center !important;

    /* FIX: primaryの白文字を上書き */
    color: #0f172a !important;
}

/* "魔法のCSS": ボタン内部のspan/divまで文字サイズを強制 */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button *,
div.element-container[class*="st-key-tile_"] div.stButton > button * {
    font-size: 20px !important;      /* FIXED: スマホで読みやすい */
    font-weight: 900 !important;
    line-height: 1.35 !important;
    white-space: pre-line !important;
    text-align: center !important;
    color: #0f172a !important;
}

div.stElementContainer[class*="st-key-tile_"] div.stButton > button:active,
div.element-container[class*="st-key-tile_"] div.stButton > button:active {
    transform: scale(0.98) !important;
    background: #f1f5f9 !important;
}

/* -----------------------------
   Back buttons (key prefix: back_)
--------------------------------*/
div.stElementContainer[class*="st-key-back_"] div.stButton > button,
div.element-container[class*="st-key-back_"] div.stButton > button {
    width: 100% !important;
    height: 52px !important;
    border-radius: 12px !important;
    background: #e2e8f0 !important;
    border: none !important;
    box-shadow: none !important;
    font-weight: 900 !important;
    color: #334155 !important;
}

/* -----------------------------
   Cards
--------------------------------*/
.card {
    background: #ffffff;
    padding: 1rem;
    border-radius: 14px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.05);
    margin-bottom: 12px;
    border-left: 6px solid #cbd5e1;
}
.card-ok { border-left-color: #22c55e !important; }
.card-ng { border-left-color: #ef4444 !important; }
.card-warn { border-left-color: #f59e0b !important; }

/* Expanders: make header easier to tap on mobile */
div[data-testid="stExpander"] summary { padding: 0.35rem 0 !important; }

</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# Logic: DB & Calculation
# ============================================================
with st.sidebar:
    st.header("⚙️ 備蓄設定")
    t_pop = st.number_input("想定人数", 1, 1_000_000, 100, 100)
    t_days = st.slider("目標日数", 1, 7, 3)
    st.info(f"目標: {t_pop:,}人 × {t_days}日分")

TARGETS = {
    "水・飲料": t_pop * 3 * t_days,
    "主食類": t_pop * 3 * t_days,
    "トイレ・衛生": t_pop * 5 * t_days,
}

db.init_db()
stocks = db.get_all_stocks() or []
today = datetime.now().date()

amounts: Dict[str, float] = {k: 0.0 for k in CATEGORIES}
expired_count = 0

def get_cat_key(c: Any) -> str:
    s = str(c or "")
    for k in CATEGORIES:
        if k in s:
            return k
    return "その他"

def iso_to_date(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s).split("T")[0])
    except Exception:
        return None

def toilet_uses(qty: Any, unit: Any) -> Optional[float]:
    u = str(unit or "").strip()
    if u in ["回", "枚", "袋", ""]:
        try:
            return float(qty)
        except Exception:
            return None
    return None

for s in stocks:
    cat = get_cat_key(s.get("category"))
    kind = str(s.get("item_kind", "stock") or "stock")
    qty = float(s.get("qty", 0) or 0)
    unit = s.get("unit", "")

    # Exclude "capacity" (durable equipment) from consumable counts, especially water
    if kind == "capacity" and cat == "水・飲料":
        continue

    if cat == "トイレ・衛生":
        uses = toilet_uses(qty, unit)
        if uses is not None:
            amounts[cat] += uses
    else:
        amounts[cat] += qty  # simplified aggregation

    d = iso_to_date(s.get("due_date"))
    if d and d < today:
        expired_count += 1

# ============================================================
# Gemini helpers
# ============================================================
@st.cache_resource(show_spinner=False)
def get_gemini_model(api_key: str):
    if genai is None:
        raise RuntimeError("google-generativeai がインストールされていません")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL)

def _extract_json_array(text: str) -> List[dict]:
    """
    Robustly extract a JSON array from Gemini output.
    - code fences
    - leading/trailing commentary
    - single quotes (fallback via ast)
    """
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)

    m = re.search(r"\[[\s\S]*\]", t)
    payload = m.group(0) if m else t

    try:
        data = json.loads(payload)
    except Exception:
        data = ast.literal_eval(payload)

    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]

def gemini_extract(pil_img: Image.Image, cat: str) -> Tuple[List[Dict[str, Any]], str]:
    if not GEMINI_API_KEY:
        return [], "No API Key"

    model = get_gemini_model(GEMINI_API_KEY)
    prompt = f"""
カテゴリ: {cat}
画像から防災備蓄品の情報を抽出し、以下のJSON配列のみを返してください（余計な文章は不要）。

[
  {{
    "name": "品名",
    "qty": 1,
    "unit": "単位(L,本,食,回,箱,基など)",
    "subtype": "トイレの場合のみ(携帯トイレ/組立トイレ/仮設トイレ/トイレ袋/凝固剤/その他)",
    "due_type": "expiry|inspection|none",
    "due_date": "YYYY-MM-DD (不明なら空文字)",
    "memo": "特徴など"
  }}
]
""".strip()

    res = model.generate_content([prompt, pil_img])
    raw = getattr(res, "text", "") or ""
    items = _extract_json_array(raw)
    return items, raw

# ============================================================
# Common UI
# ============================================================
def back_home(key_suffix: str) -> None:
    if button_stretch("🔙 ホームに戻る", key=f"back_{key_suffix}", type="secondary"):
        st.session_state.inv_cat = None
        navigate_to("home")

def render_card(code: str, title: str, ok: bool, ev_html: str) -> None:
    cls = "card-ok" if ok else "card-ng"
    st.markdown(
        f'<div class="card {cls}"><b>{code} {title}</b><br>'
        f'判定: {"🟢 適合" if ok else "🔴 不適合"}<br>'
        f'<small>{ev_html}</small></div>',
        unsafe_allow_html=True,
    )

# ============================================================
# Router
# ============================================================
page = st.session_state.current_page

# ============================================================
# 🏠 Home
# ============================================================
if page == "home":
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown(
        "<p style='text-align:center; color:#64748b; margin: 0 0 16px 0;'>物資DX台帳 × 自主点検</p>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        if button_stretch("📊\n分析レポート\n(充足率)", key="tile_dash", type="primary"):
            navigate_to("dashboard")
        if button_stretch("✅\n自動自主点検\n(裏取り)", key="tile_insp", type="primary"):
            navigate_to("inspection")
    with c2:
        if button_stretch("📦\n備蓄・登録\n(現場)", key="tile_inv", type="primary"):
            navigate_to("inventory")
        if button_stretch("💾\nデータ管理\n(CSV)", key="tile_data", type="primary"):
            navigate_to("data")

    st.markdown("---")
    if expired_count:
        st.error(f"🚨 期限切れが {expired_count} 件あります")
    else:
        st.success("✅ 期限切れはありません")

# ============================================================
# 📊 Dashboard
# ============================================================
elif page == "dashboard":
    back_home("dash")
    st.markdown("## 📊 充足率")

    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        denom = float(TARGETS.get(k, 0) or 0)
        pct = min((amounts[k] / denom), 1.0) if denom > 0 else 0.0
        st.write(f"**{k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k]):,} / 目標: {int(denom):,}（{int(pct*100)}%）")

# ============================================================
# ✅ Inspection
# ============================================================
elif page == "inspection":
    back_home("insp")
    st.markdown("## ✅ 自動点検")

    with st.expander("🏢 施設情報 (任意)", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 10_000, 0, key="f_toilets")

    # 6-5 logic (portable uses + booth counts)
    portable_uses = float(amounts.get("トイレ・衛生", 0) or 0)

    units_total = float(f_toilets) + sum(
        float(s.get("qty", 0) or 0)
        for s in stocks
        if get_cat_key(s.get("category")) == "トイレ・衛生"
        and (s.get("subtype") in ["仮設トイレ", "組立トイレ"])
    )

    # Required uses: at least 3 days, or user-selected t_days (whichever larger)
    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days)

    # Required booths: short-term 50 ppl/booth, long-term 20 ppl/booth (simple rule)
    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20

    ok_uses = portable_uses >= need_uses
    ok_units = units_total >= need_units

    msg_65 = (
        f"携帯トイレ等(回): {int(portable_uses):,} / 必要: {int(need_uses):,}<br>"
        f"トイレ基数(基): {int(units_total):,} / 必要: {int(need_units):,}<br>"
        f"※ 基数 = 既設 + 仮設 + 組立"
    )
    render_card("6-5", "簡易トイレ等の備え", (ok_uses and ok_units), msg_65)

    w_ok = amounts["水・飲料"] >= TARGETS["水・飲料"]
    w_pct = int((amounts["水・飲料"] / TARGETS["水・飲料"]) * 100) if TARGETS["水・飲料"] > 0 else 0
    render_card("7-1", "水・食料の備え（簡易）", w_ok, f"水: {int(amounts['水・飲料']):,} / 目標: {int(TARGETS['水・飲料']):,}（{w_pct}%）")

# ============================================================
# 📦 Inventory
# ============================================================
elif page == "inventory":
    back_home("inv")

    # Category selection (tile buttons)
    if st.session_state.inv_cat is None:
        st.markdown("## 📦 カテゴリ選択")
        cols = st.columns(2)
        for i, (cat, icon) in enumerate(CATEGORIES.items()):
            with cols[i % 2]:
                label = f"{icon}\n{cat}\n{int(amounts[cat]):,}"
                if button_stretch(label, key=f"tile_cat_{cat}", type="primary"):
                    st.session_state.inv_cat = cat
                    st.rerun()

    else:
        cat = st.session_state.inv_cat
        st.markdown(f"## {CATEGORIES[cat]} {cat}")

        if button_stretch("🔙 カテゴリ一覧に戻る", key="back_cat", type="secondary"):
            st.session_state.inv_cat = None
            st.rerun()

        tab1, tab2 = st.tabs(["📸 AI登録", "📝 リスト"])

        with tab1:
            if genai is None:
                st.warning("google-generativeai が未インストールです。requirements.txt を確認してください。")
            elif not GEMINI_API_KEY:
                st.warning("GEMINI_API_KEY が未設定です（環境変数 or .env）")

            img_file = st.camera_input("撮影（iPhone対応）")
            if not img_file:
                img_file = st.file_uploader("または画像アップロード", type=["jpg", "jpeg", "png"])

            if img_file and st.button("解析開始", key=f"run_ai_{cat}", type="primary"):
                with st.spinner("AI解析中..."):
                    try:
                        pil_img = Image.open(img_file)
                        items, raw = gemini_extract(pil_img, cat)
                        st.session_state.ai_last_raw = raw

                        if not items:
                            st.error("AIの返却が空でした。写真のブレや写り込みを確認してください。")
                        else:
                            to_insert: List[Dict[str, Any]] = []
                            for it in items:
                                to_insert.append({
                                    "name": str(it.get("name", "")).strip() or "（品名未設定）",
                                    "qty": float(it.get("qty", 1) or 1),
                                    "category": cat,
                                    "unit": str(it.get("unit", "") or "").strip(),
                                    "subtype": str(it.get("subtype", "") or "").strip(),
                                    "due_type": str(it.get("due_type", "none") or "none").strip(),
                                    "due_date": str(it.get("due_date", "") or "").strip(),
                                    "memo": str(it.get("memo", "") or "").strip(),
                                    "item_kind": "stock",
                                })
                            db.bulk_upsert(to_insert)
                            st.success(f"登録しました（{len(to_insert)}件）")
                            st.rerun()
                    except Exception as e:
                        st.error(f"エラー: {e}")
                        with st.expander("AI raw output（デバッグ）", expanded=False):
                            st.code(st.session_state.get("ai_last_raw", ""), language="text")

        with tab2:
            rows = [s for s in stocks if get_cat_key(s.get("category")) == cat]
            if not rows:
                st.info("まだ在庫がありません。")

            for s in rows:
                qty = int(float(s.get("qty", 0) or 0))
                title = f"{s.get('name','(no name)')} (×{qty})"
                with st.expander(title):
                    if s.get("unit"):
                        st.write(f"単位: {s.get('unit','')}")
                    if s.get("subtype"):
                        st.write(f"種別: {s.get('subtype')}")
                    if s.get("due_date"):
                        st.write(f"{DUE_LABEL.get(s.get('due_type','none'), s.get('due_type','none'))}: {s.get('due_date')}")
                    if s.get("memo"):
                        st.caption(s.get("memo"))

                    if st.button("削除", key=f"del_{s.get('id')}"):
                        db.delete_stock(s.get("id"))
                        st.rerun()

# ============================================================
# 💾 Data
# ============================================================
elif page == "data":
    back_home("data")
    st.markdown("## 💾 データ管理")

    df = pd.DataFrame(stocks)
    st.download_button(
        "📥 CSV保存",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name="backup.csv",
        mime="text/csv",
    )

else:
    # Fallback
    st.session_state.current_page = "home"
    st.rerun()
