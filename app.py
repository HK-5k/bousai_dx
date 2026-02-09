import os
import re
import html
import sqlite3
import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import io
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

import db

# --- 設定 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY and os.path.exists(".env"):
    try:
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                    GEMINI_API_KEY = line.split("=", 1)[1].strip().strip('"\'')
                    break
    except Exception:
        pass

st.set_page_config(
    page_title="香川防災DX",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- ページ状態管理 ---
if 'current_page' not in st.session_state:
    st.session_state.current_page = "home"

def navigate_to(page_name):
    st.session_state.current_page = page_name
    st.rerun()

# --- CSS（バランスを完璧に整える） ---
st.markdown("""
<style>
/* 全体の背景と中央寄せ */
.stApp { background-color: #f8f9fa; }
.block-container { 
    padding-top: 2rem !important; 
    max-width: 500px !important; 
    margin: 0 auto !important;
}

/* タイトル */
h1, h2 { 
    text-align: center;
    font-family: "Helvetica Neue", Arial, sans-serif; 
    color: #333; 
    font-weight: 800;
}

/* --- ボタンを2列に綺麗に並べるための設定 --- */
div.stButton > button {
    width: 100% !important;
    height: 140px !important; /* 高さをしっかり出す */
    background-color: white !important;
    border: 1px solid #eee !important;
    border-radius: 20px !important;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05) !important;
    color: #333 !important;
    font-weight: bold !important;
    font-size: 1rem !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    white-space: pre-wrap !important;
    line-height: 1.5 !important;
    margin-bottom: 0px !important;
    transition: all 0.2s !important;
}

/* ボタン内の改行と余白を制御 */
div.stButton > button p {
    margin-top: 10px !important;
}

div.stButton > button:active {
    transform: scale(0.95) !important;
    background-color: #f0f0f0 !important;
}

/* 戻るボタン専用スタイル（横長に） */
.back-container div.stButton > button {
    height: 50px !important;
    border-radius: 12px !important;
    font-size: 0.9rem !important;
    background-color: #eee !important;
    box-shadow: none !important;
    margin-bottom: 20px !important;
}

/* スコア表示 */
.score-circle {
    width: 140px; height: 140px; border-radius: 50%;
    background: conic-gradient(#007bff var(--p), #eee 0deg);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 15px auto; font-size: 2.5rem; font-weight: bold; color: #007bff;
    position: relative;
    box-shadow: inset 0 0 20px rgba(0,0,0,0.05);
}
.score-circle::after { content: attr(data-score); position: absolute; }

/* 状態バッジ */
.status-msg {
    text-align: center;
    padding: 12px;
    border-radius: 15px;
    margin-top: 20px;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# --- 避難所シミュレーション設定（サイドバー） ---
with st.sidebar:
    st.header("⚙️ 避難所設定")
    target_pop = st.number_input("避難想定人数 (人)", 10, 5000, 100, 10)
    target_days = st.slider("備蓄目標日数 (日)", 1, 7, 3)
    st.info(f"目標: {target_pop}人 × {target_days}日分")

# --- 定数 ---
CATEGORIES = {"水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽", "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"}
TARGETS = {
    "水・飲料": target_pop * 3 * target_days,
    "主食類": target_pop * 3 * target_days,
    "トイレ・衛生": target_pop * 5 * target_days,
}

# --- データ集計 ---
db.init_db()
stocks = db.get_all_stocks() or []
today = datetime.now().date()
amounts = {k: 0 for k in CATEGORIES}

def get_cat_key(cat):
    for k in CATEGORIES.keys():
        if k in str(cat): return k
    return "その他"

for s in stocks:
    k = get_cat_key(s.get('category',''))
    try: amounts[k] += float(s.get('qty', 0))
    except: pass

# ==========================================
# 🏠 ホーム画面
# ==========================================
if st.session_state.current_page == "home":
    st.markdown("## ⛑️ 香川防災DX")
    
    # --- 2列のグリッド配置 ---
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊\n分析レポート\n(充足率スコア)", key="btn_dash"):
            navigate_to("dashboard")
        st.write("") # スペース
        if st.button("✅\n自動自主点検\n(○△×判定)", key="btn_check"):
            navigate_to("inspection")

    with c2:
        if st.button("📦\n備蓄・登録\n(カテゴリ別)", key="btn_inv"):
            navigate_to("inventory")
        st.write("") # スペース
        if st.button("💾\nデータ管理\n(CSV入出力)", key="btn_data"):
            navigate_to("data")

    # 期限切れチェック
    expired = [s for s in stocks if (d := re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(s.get('memo','')))) and datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))).date() < today]
    
    if expired:
        st.error(f"⚠️ {len(expired)}件の期限切れがあります")
    else:
        st.success("✅ 全て有効期限内です")

# ==========================================
# 📊 その他のページ (省略)
# ==========================================
# ※ 他のページのロジックは以前と同様です。
elif st.session_state.current_page == "dashboard":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_dash"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    # (分析レポートのコンテンツ...)

elif st.session_state.current_page == "inventory":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_inv"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    # (在庫登録のコンテンツ...)

elif st.session_state.current_page == "inspection":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_insp"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    # (自動点検のコンテンツ...)

elif st.session_state.current_page == "data":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_data"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    # (データ管理のコンテンツ...)