import os
import re
import sqlite3
import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
from datetime import datetime
import time

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
    except: pass

st.set_page_config(
    page_title="香川防災DX",
    layout="centered", # 画面中央に寄せる
    initial_sidebar_state="collapsed",
)

# --- 状態管理 ---
if 'current_page' not in st.session_state:
    st.session_state.current_page = "home"

def navigate_to(page_name):
    st.session_state.current_page = page_name
    # Rerunは最後に行う

# --- CSS（2列配置と真っ白エラー防止） ---
st.markdown("""
<style>
/* 画面全体の幅制限と中央寄せ */
.block-container {
    max-width: 500px !important;
    padding-top: 2rem !important;
}

/* タイトル中央寄せ */
h1, h2 { text-align: center; }

/* ボタンをタイル状にする設定 */
div.stButton > button {
    width: 100% !important;
    height: 140px !important;
    background-color: white !important;
    border: 1px solid #ddd !important;
    border-radius: 20px !important;
    box-shadow: 0 4px 10px rgba(0,0,0,0.05) !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    white-space: pre-wrap !important;
    line-height: 1.4 !important;
    font-weight: bold !important;
    margin-bottom: 10px !important;
}

/* 小さい戻るボタン用 */
.back-btn div.stButton > button {
    height: 50px !important;
    border-radius: 12px !important;
    background-color: #f0f0f0 !important;
    box-shadow: none !important;
}
</style>
""", unsafe_allow_html=True)

# --- データ取得 ---
db.init_db()
stocks = db.get_all_stocks() or []

# ==========================================
# 🏠 ホーム画面 (真っ白にならないように独立)
# ==========================================
if st.session_state.current_page == "home":
    st.markdown("## ⛑️ 香川防災DX")
    
    # 2列を確実に作る
    c1, c2 = st.columns(2)
    
    with c1:
        if st.button("📊\n分析レポート\n(充足率スコア)", key="btn_dash"):
            st.session_state.current_page = "dashboard"
            st.rerun()
        
        if st.button("✅\n自動自主点検\n(○△×判定)", key="btn_check"):
            st.session_state.current_page = "inspection"
            st.rerun()

    with c2:
        if st.button("📦\n備蓄・登録\n(カテゴリ別)", key="btn_inv"):
            st.session_state.current_page = "inventory"
            st.rerun()
            
        if st.button("💾\nデータ管理\n(CSV入出力)", key="btn_data"):
            st.session_state.current_page = "data"
            st.rerun()

    st.write("---")
    st.success("✅ システム稼働中")

# ==========================================
# 📊 その他のページ (中身が空だと真っ白になるのでダミーを配置)
# ==========================================
else:
    # 戻るボタンエリア
    st.markdown('<div class="back-btn">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る"):
        st.session_state.current_page = "home"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.current_page == "dashboard":
        st.subheader("📊 分析レポート")
        st.info("集計データを読み込んでいます...")
        # ここに充足率のロジックを戻す

    elif st.session_state.current_page == "inventory":
        st.subheader("📦 備蓄・登録")
        st.write("カテゴリを選んでください。")

    elif st.session_state.current_page == "inspection":
        st.subheader("✅ 自動自主点検")
        st.write("点検項目を確認しています...")

    elif st.session_state.current_page == "data":
        st.subheader("💾 データ管理")
        st.download_button("CSV出力", data=pd.DataFrame(stocks).to_csv().encode('utf-8-sig'), file_name="backup.csv")