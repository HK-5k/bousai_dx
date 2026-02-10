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
    initial_sidebar_state="collapsed",
)

# =========================
# UI Helper (スマホ対策)
# =========================
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters

def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    """ボタンを横幅いっぱいに広げるヘルパー"""
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

BASE_UNIT = {"水・飲料": "L", "主食類": "食", "トイレ・衛生": "回"}

# =========================
# CSS: デザイン修正の心臓部
# =========================
st.markdown(
    """
<style>
/* iOSの文字サイズ自動調整を無効化 */
html { -webkit-text-size-adjust: 100%; }

.stApp { background-color: #f8fafc; }
.block-container { 
    max-width: 600px !important; 
    margin: 0 auto !important; 
    padding: 1rem 1rem 3rem 1rem !important; 
}
h2 { text-align: center; font-weight: 900; color: #0f172a; margin-bottom: 1.5rem !important; }

/* --- タイルボタン：keyが tile_ で始まるものだけを巨大化 --- */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button,
div.element-container[class*="st-key-tile_"] div.stButton > button {
    width: 100% !important;
    height: auto !important;
    min-height: clamp(120px, 22vw, 170px) !important; /* スマホ幅に応じて伸縮 */
    padding: clamp(14px, 3.5vw, 22px) !important;
    
    border-radius: 18px !important;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
    box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08) !important;
    
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    flex-direction: column !important;
}

/* ★ここが重要：ボタン内部のテキストサイズ強制適用 */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button *,
div.element-container[class*="st-key-tile_"] div.stButton > button * {
    font-size: clamp(16px, 4.5vw, 22px) !important; /* 文字も大きく */
    font-weight: 800 !important;
    line-height: 1.4 !important;
    white-space: pre-line !important; /* 改行を有効化 */
    text-align: center !important;
}

/* 押した時の沈み込み */
div.stElementContainer[class*="st-key-tile_"] div.stButton > button:active,
div.element-container[class*="st-key-tile_"] div.stButton > button:active {
    transform: scale(0.98) !important;
    background: #f8fafc !important;
}

/* --- 戻るボタン：keyが back_ で始まるものだけ統一 --- */
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

/* カードUI */
.card { background: white; padding: 1rem; border-radius: 14px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); margin-bottom: 12px; border-left: 6px solid #ccc; }
.card-ok { border-left-color: #22c55e !important; }
.card-ng { border-left-color: #ef4444 !important; }
.card-warn { border-left-color: #f59e0b !important; }

#MainMenu {visibility:hidden;} footer {visibility:hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# =========================
# Logic & Data (v3機能維持)
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

# --- Helper Functions ---
def get_cat_key(c): return next((k for k in CATEGORIES if k in str(c)), "その他")
def iso_to_date(s): 
    try: return date.fromisoformat(str(s).split("T")[0]) 
    except: return None

def toilet_uses(qty, unit):
    u = str(unit).strip()
    if u in ["回", "枚", "袋"]: return float(qty)
    return None

# --- Aggregation ---
for s in stocks:
    cat = get_cat_key(s.get("category"))
    kind = s.get("item_kind", "stock")
    qty = float(s.get("qty", 0))
    unit = s.get("unit", "")
    
    if kind == "capacity" and cat == "水・飲料":
        water_capacity.append(s)
        continue
        
    if cat == "トイレ・衛生":
        if (uses := toilet_uses(qty, unit)) is not None:
            amounts[cat] += uses
    elif cat == "水・飲料":
        amounts[cat] += qty 
    else:
        amounts[cat] += qty

    if (d := iso_to_date(s.get("due_date"))) and d < today:
        expired_count += 1

# =========================
# Pages
# =========================
def back_home(key_suffix):
    # keyを "back_" で始めることでCSSを適用
    if button_stretch("🔙 ホームに戻る", key=f"back_{key_suffix}", type="secondary"): 
        navigate_to("home")

if st.session_state.current_page == "home":
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown("<p style='text-align:center; color:#64748b; margin-top:-10px; margin-bottom:20px;'>物資DX台帳 × 自主点検システム</p>", unsafe_allow_html=True)
    
    # keyを "tile_" で始めることで、CSSによる巨大化・自動調整を適用
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
    
    if expired_count: st.error(f"🚨 期限切れが {expired_count} 件あります")
    else: st.success("✅ 期限切れはありません")

elif st.session_state.current_page == "inspection":
    back_home("insp")
    st.markdown("## ✅ 自動点検 (v3準拠)")
    
    with st.expander("🏢 施設情報 (任意)", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 100, 0, key="f_toilets")

    def card(code, title, ok, ev):
        cls = "card-ok" if ok else "card-ng"
        st.markdown(f'<div class="card {cls}"><b>{code} {title}</b><br>判定: {"🟢 適合" if ok else "🔴 不適合"}<br><small>{ev}</small></div>', unsafe_allow_html=True)

    # 6-5 Logic (Simplified for stability)
    portable_uses = amounts["トイレ・衛生"]
    
    # 基数集計
    units_total = f_toilets + sum(s['qty'] for s in stocks if get_cat_key(s['category']) == "トイレ・衛生" and s.get('subtype') in ["仮設トイレ", "組立トイレ"])
    
    # 判定
    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days) # 最低3日分
    ok_uses = portable_uses >= need_uses
    
    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20
    ok_units = units_total >= need_units
    
    msg = f"携帯トイレ(回): {int(portable_uses):,} / 必要: {int(need_uses):,}\n"
    msg += f"トイレ基数(基): {int(units_total)} / 必要: {need_units}\n"
    msg += "※ 既設・仮設・組立の合計"
    
    card("6-5", "簡易トイレ等の備え", (ok_uses and ok_units), msg)
    card("7-1", "水・食料の備え", amounts["水・飲料"] >= TARGETS["水・飲料"], f"水充足率: {int(amounts['水・飲料']/TARGETS['水・飲料']*100)}%")

elif st.session_state.current_page == "dashboard":
    back_home("dash")
    st.markdown("## 📊 充足率")
    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        pct = min(amounts[k]/TARGETS[k], 1.0)
        st.write(f"**{k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k])} / 目標: {int(TARGETS[k])}")

elif st.session_state.current_page == "inventory":
    back_home("inv")
    st.markdown("## 📦 在庫・登録")
    st.info("（ここにAI登録機能が入ります・v2準拠）")
    # 簡易実装のため省略。必要なら inventory 部分のみ詳細追加します

elif st.session_state.current_page == "data":
    back_home("data")
    st.markdown("## 💾 データ管理")
    # ↓ここで utf-8-sig に修正済み（重要！）
    st.download_button("CSV保存", pd.DataFrame(stocks).to_csv().encode('utf-8-sig'), "backup.csv")