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
    genai = None

import db

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
    initial_sidebar_state="collapsed",
)

# =========================
# UI Helper
# =========================
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters

def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    if _SUPPORTS_WIDTH:
        return st.button(label, key=key, type=type, width="stretch", **kwargs)
    return st.button(label, key=key, type=type, use_container_width=True, **kwargs)

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

# =========================
# CSS (v4.1：文字消え＆見切れ修正)
# =========================
st.markdown(
    """
<style>
html { -webkit-text-size-adjust: 100%; }

.stApp { background-color: #f8fafc; }

/* ★iPhoneノッチ対策：safe-areaを加味 */
.block-container { 
    max-width: 600px !important; 
    margin: 0 auto !important; 
    padding-top: calc(1rem + env(safe-area-inset-top)) !important;
    padding-right: calc(1rem + env(safe-area-inset-right)) !important;
    padding-bottom: calc(3rem + env(safe-area-inset-bottom)) !important;
    padding-left: calc(1rem + env(safe-area-inset-left)) !important;
}

h2 { 
    text-align: center; 
    font-weight: 900; 
    color: #0f172a; 
    margin-top: 0.25rem !important;
    margin-bottom: 1.5rem !important; 
}

/* --- タイルボタン（tile_） --- */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button,
div.element-container[class*="st-key-tile_"] div.stButton > button {
    width: 100% !important;
    height: auto !important;
    min-height: clamp(120px, 22vw, 170px) !important;
    padding: clamp(14px, 3.5vw, 22px) !important;

    border-radius: 18px !important;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
    box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08) !important;

    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    flex-direction: column !important;

    /* ★ここが本丸：primaryの白文字を上書き */
    color: #0f172a !important;
}

/* ★内側のspan/divにも色とサイズを強制 */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button *,
div.element-container[class*="st-key-tile_"] div.stButton > button * {
    font-size: clamp(16px, 4.5vw, 22px) !important;
    font-weight: 800 !important;
    line-height: 1.4 !important;
    white-space: pre-line !important;
    text-align: center !important;

    /* ★これが無いと内側が白のままになることがある */
    color: #0f172a !important;
}

div.stElementContainer[class*="st-key-tile_"] div.stButton > button:active,
div.element-container[class*="st-key-tile_"] div.stButton > button:active {
    transform: scale(0.98) !important;
    background: #f8fafc !important;
}

/* --- 戻るボタン（back_） --- */
div.stElementContainer[class*="st-key-back_"] div.stButton > button,
div.element-container[class*="st-key-back_"] div.stButton > button {
    width: 100% !important;
    height: 48px !important;
    border-radius: 12px !important;
    background: #e2e8f0 !important;
    border: none !important;
    box-shadow: none !important;
    font-weight: 800 !important;
    color: #475569 !important;
}

/* カード */
.card { background: white; padding: 1rem; border-radius: 14px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); margin-bottom: 12px; border-left: 6px solid #ccc; }
.card-ok { border-left-color: #22c55e !important; }
.card-ng { border-left-color: #ef4444 !important; }
.card-warn { border-left-color: #f59e0b !important; }

#MainMenu {visibility:hidden;}
footer {visibility:hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# =========================
# Data
# =========================
with st.sidebar:
    st.header("⚙️ 備蓄設定")
    t_pop = st.number_input("想定人数", 1, 1000000, 100, 100)
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

amounts = {k: 0.0 for k in CATEGORIES}
water_capacity = []
expired_count = 0

def get_cat_key(c): 
    return next((k for k in CATEGORIES if k in str(c)), "その他")

def iso_to_date(s): 
    try: 
        return date.fromisoformat(str(s).split("T")[0]) 
    except: 
        return None

def toilet_uses(qty, unit):
    u = str(unit or "").strip()
    if u in ["回", "枚", "袋", ""]: 
        try: return float(qty)
        except: return None
    return None

for s in stocks:
    cat = get_cat_key(s.get("category"))
    kind = str(s.get("item_kind", "stock") or "stock")
    qty = float(s.get("qty", 0) or 0)
    unit = s.get("unit", "")
    
    if kind == "capacity" and cat == "水・飲料":
        water_capacity.append(s)
        continue
        
    if cat == "トイレ・衛生":
        uses = toilet_uses(qty, unit)
        if uses is not None:
            amounts[cat] += uses
    else:
        amounts[cat] += qty

    d = iso_to_date(s.get("due_date"))
    if d and d < today:
        expired_count += 1

# =========================
# Pages
# =========================
def back_home(key_suffix):
    if button_stretch("🔙 ホームに戻る", key=f"back_{key_suffix}", type="secondary"):
        st.session_state.inv_cat = None
        navigate_to("home")

if st.session_state.current_page == "home":
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown(
        "<p style='text-align:center; color:#64748b; margin-top:-10px; margin-bottom:20px;'>物資DX台帳 × 自主点検システム</p>",
        unsafe_allow_html=True
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

elif st.session_state.current_page == "inspection":
    back_home("insp")
    st.markdown("## ✅ 自動点検 (v4.1)")

    with st.expander("🏢 施設情報 (任意)", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 1000, 0, key="f_toilets")

    def card(code, title, ok, ev_html):
        cls = "card-ok" if ok else "card-ng"
        st.markdown(
            f'<div class="card {cls}"><b>{code} {title}</b><br>'
            f'判定: {"🟢 適合" if ok else "🔴 不適合"}<br>'
            f'<small>{ev_html}</small></div>',
            unsafe_allow_html=True
        )

    portable_uses = amounts["トイレ・衛生"]
    units_total = float(f_toilets) + sum(
        float(s.get("qty", 0) or 0)
        for s in stocks
        if get_cat_key(s.get("category")) == "トイレ・衛生"
        and (s.get("subtype") in ["仮設トイレ", "組立トイレ"])
    )

    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days)  # 最低3日分
    ok_uses = portable_uses >= need_uses

    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20
    ok_units = units_total >= need_units

    msg = (
        f"携帯トイレ(回): {int(portable_uses):,} / 必要: {int(need_uses):,}<br>"
        f"トイレ基数(基): {int(units_total):,} / 必要: {int(need_units):,}<br>"
        f"※ 既設 + 仮設 + 組立 の合計"
    )
    card("6-5", "簡易トイレ等の備え", (ok_uses and ok_units), msg)

    w_ok = amounts["水・飲料"] >= TARGETS["水・飲料"]
    w_pct = int((amounts["水・飲料"] / TARGETS["水・飲料"]) * 100) if TARGETS["水・飲料"] > 0 else 0
    card("7-1", "水・食料の備え（簡易）", w_ok, f"水: {int(amounts['水・飲料']):,} / 目標: {int(TARGETS['水・飲料']):,}（{w_pct}%）")

elif st.session_state.current_page == "dashboard":
    back_home("dash")
    st.markdown("## 📊 充足率")
    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        pct = min(amounts[k] / TARGETS[k], 1.0) if TARGETS[k] else 0
        st.write(f"**{k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k]):,} / 目標: {int(TARGETS[k]):,}")

elif st.session_state.current_page == "inventory":
    back_home("inv")
    st.markdown("## 📦 在庫・登録")
    st.info("（ここにAI登録機能が入ります・v2/v3準拠で戻せます）")

elif st.session_state.current_page == "data":
    back_home("data")
    st.markdown("## 💾 データ管理")
    st.download_button("CSV保存", pd.DataFrame(stocks).to_csv(index=False).encode("utf-8-sig"), "backup.csv")