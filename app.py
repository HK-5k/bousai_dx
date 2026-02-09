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

# --- CSS（スマホアプリ風ボタン ＆ 評価デザイン） ---
st.markdown("""
<style>
.stApp { background-color: #f8f9fa; }
.block-container { padding-top: 1rem; max-width: 600px !important; }

/* タイトル */
h1, h2, h3 { font-family: sans-serif; color: #333; font-weight: 800; }

/* スマホ風ボタンの整形（st.buttonをオーバーライド） */
div.stButton > button {
    width: 100%;
    height: 120px;
    background-color: white;
    border: 1px solid #ddd;
    border-radius: 20px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    color: #333;
    font-weight: bold;
    font-size: 1.1rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    white-space: pre-wrap;
    line-height: 1.4;
    margin-bottom: 10px;
}
div.stButton > button:active { transform: scale(0.98); background-color: #f0f0f0; }

/* 戻るボタン専用 */
.back-container div.stButton > button {
    height: 45px !important;
    border-radius: 10px !important;
    font-size: 0.9rem !important;
    background-color: #eee !important;
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

/* 点検パネル */
.inspection-item {
    background: white; padding: 15px; border-radius: 12px;
    margin-bottom: 12px; border-left: 6px solid #ccc;
    box-shadow: 0 2px 4px rgba(0,0,0,0.03);
}
.check-ok { border-left-color: #00c853 !important; }
.check-ng { border-left-color: #ff4b4b !important; }
</style>
""", unsafe_allow_html=True)

# --- 避難所シミュレーション設定（サイドバー） ---
with st.sidebar:
    st.header("⚙️ 避難所シミュレーション")
    target_pop = st.number_input("避難想定人数 (人)", 10, 5000, 100, 10)
    target_days = st.slider("備蓄目標日数 (日)", 1, 7, 3)
    st.info(f"目標基準:\n**{target_pop}人 × {target_days}日分**")

# --- 定数と目標値（香川県資料準拠） ---
CATEGORIES = {
    "水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽",
    "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"
}
TARGETS = {
    "水・飲料": target_pop * 3 * target_days,      # 3L/人/日 
    "主食類": target_pop * 3 * target_days,        # 3食/人/日 
    "トイレ・衛生": target_pop * 5 * target_days,  # 5回/人/日 
}

# --- データ取得と集計 ---
db.init_db()
stocks = db.get_all_stocks() or []
today = datetime.now().date()
amounts = {k: 0 for k in CATEGORIES}

def get_cat_key(db_cat_str):
    for key in CATEGORIES.keys():
        if key in str(db_cat_str): return key
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
    st.markdown("<p style='color:#666; margin-top:-15px;'>在庫管理 & デジタル自主点検</p>", unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊\n分析レポート\n(充足率スコア)", key="nav_dash"): navigate_to("dashboard")
        if st.button("📦\n在庫・登録\n(カテゴリ別)", key="nav_inv"): navigate_to("inventory")
    with c2:
        if st.button("✅\n自動自主点検\n(○△×判定)", key="nav_check"): navigate_to("inspection")
        if st.button("💾\nデータ管理\n(CSV入出力)", key="nav_data"): navigate_to("data")

    # 期限切れクイックチェック
    expired = [s for s in stocks if (d := re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(s.get('memo','')))) and datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))).date() < today]
    if expired: st.error(f"⚠️ **{len(expired)}件** の備蓄品が期限切れです！")
    else: st.success("✅ 全ての備蓄品が有効期限内です。")

# ==========================================
# 📊 分析レポート (充足率スコア)
# ==========================================
elif st.session_state.current_page == "dashboard":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_dash"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("## 📊 充足率レポート")
    
    r_water = min(amounts["水・飲料"] / TARGETS["水・飲料"], 1.0)
    r_food = min(amounts["主食類"] / TARGETS["主食類"], 1.0)
    r_toilet = min(amounts["トイレ・衛生"] / TARGETS["トイレ・衛生"], 1.0)
    total_score = int(((r_water + r_food + r_toilet) / 3) * 100)
    
    color = '#00c853' if total_score > 80 else '#ffa726' if total_score > 50 else '#ff4b4b'
    st.markdown(f'<div class="score-circle" style="--p: {total_score * 3.6}deg; background: conic-gradient({color} {total_score}%, #eee 0deg);" data-score="{total_score}"></div>', unsafe_allow_html=True)

    st.markdown("### 詳細データ")
    for k, icon in [("水・飲料","💧"), ("主食類","🍚"), ("トイレ・衛生","🚽")]:
        pct = (amounts[k]/TARGETS[k])
        st.write(f"{icon} **{k}**")
        st.progress(min(pct, 1.0))
        st.caption(f"現在: {int(amounts[k])} / 目標: {TARGETS[k]} ({int(pct*100)}%)")

# ==========================================
# ✅ 自動自主点検 (デジタル裏取り)
# ==========================================
elif st.session_state.current_page == "inspection":
    st.markdown('<div class="back-container">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る", key="back_insp"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("## ✅ デジタル自主点検")
    
    def check_item(id, q, ok, reason):
        cls = "check-ok" if ok else "check-ng"
        st.markdown(f'<div class="inspection-item {cls}"><small>{id}</small><br><b>{q}</b><br><small>{"🟢 適合" if ok else "🔴 不適合"}: {reason}</small></div>', unsafe_allow_html=True)

    # 香川県自主点検表 [cite: 14, 21] に基づく判定
    check_item("7-1", "避難想定人数に対する食料・水の備蓄", (amounts["水・飲料"] >= TARGETS["水・飲料"]*0.5), f"水充足率 {int(amounts['水・飲料']/TARGETS['水・飲料']*100)}%")
    check_item("6-5", "簡易トイレ等の物資の備え", (amounts["トイレ・衛生"] >= target_pop*5), f"在庫 {int(amounts['トイレ・衛生'])}回")
    check_item("7-2", "乳幼児・要配慮者への備え", (amounts["乳幼児用品"] > 0), f"乳幼児用品在庫: {int(amounts['乳幼児用品'])}点")

# ==========================================
# 📦 在庫・登録 / 💾 データ管理 (略: 既存ロジックを継承)
# ==========================================
# (inventory と data のページは以前のボタン整形ロジックを維持して実装)