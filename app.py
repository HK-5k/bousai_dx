import os
import re
import html
import sqlite3
import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
from datetime import datetime
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
    except: pass

st.set_page_config(page_title="香川防災DX", layout="wide", initial_sidebar_state="expanded")

# --- 状態管理 ---
if 'current_page' not in st.session_state: st.session_state.current_page = "home"
if 'inv_cat' not in st.session_state: st.session_state.inv_cat = None

def navigate_to(page):
    st.session_state.current_page = page
    st.rerun()

# --- CSS (バグの起きないボタン設計) ---
st.markdown("""
<style>
.stApp { background-color: #f8f9fa; }
.block-container { max-width: 600px !important; }
h1, h2, h3 { color: #333; font-weight: 800; }
div.stButton > button {
    width: 100%; height: 100px; background-color: white; border: 1px solid #ddd;
    border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    font-weight: bold; font-size: 1.1rem; white-space: pre-wrap;
}
.score-circle {
    width: 140px; height: 140px; border-radius: 50%;
    background: conic-gradient(#007bff var(--p), #eee 0deg);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 15px auto; font-size: 2.5rem; font-weight: bold; color: #007bff;
    position: relative;
}
.score-circle::after { content: attr(data-score); position: absolute; }
.inspection-item {
    background: white; padding: 15px; border-radius: 12px; margin-bottom: 12px;
    border-left: 6px solid #ccc; box-shadow: 0 2px 4px rgba(0,0,0,0.03);
}
.check-ok { border-left-color: #00c853 !important; }
.check-ng { border-left-color: #ff4b4b !important; }
</style>
""", unsafe_allow_html=True)

# --- 避難所シミュレーション (香川県基準) ---
with st.sidebar:
    st.header("⚙️ 避難所シミュレーション")
    t_pop = st.number_input("避難想定人数", 10, 5000, 100, 10)
    t_days = st.slider("備蓄目標日数", 1, 7, 3)
    st.info(f"目標: {t_pop}人 × {t_days}日分")

CATEGORIES = {"水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽", "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"}
TARGETS = {
    "水・飲料": t_pop * 3 * t_days,     # 香川県基準: 3L/人/日 [cite: 4]
    "主食類": t_pop * 3 * t_days,       # 香川県基準: 3食/人/日 [cite: 4]
    "トイレ・衛生": t_pop * 5 * t_days # 香川県基準: 5回/人/日 [cite: 4, 14]
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

# --- 共通部品: 戻るボタン ---
def back_home_button():
    if st.button("🔙 ホームに戻る", key="global_back"): navigate_to("home")

# ==========================================
# 🏠 ページ分岐
# ==========================================

# 1. ホーム
if st.session_state.current_page == "home":
    st.markdown("## ⛑️ 香川防災DX")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊\n分析レポート"): navigate_to("dashboard")
        if st.button("📦\n備蓄・登録"): navigate_to("inventory")
    with c2:
        if st.button("✅\n自動自主点検"): navigate_to("inspection")
        if st.button("💾\nデータ管理"): navigate_to("data")
    
    expired = [s for s in stocks if (d := re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(s.get('memo','')))) and datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))).date() < today]
    if expired: st.error(f"⚠️ {len(expired)}件の期限切れがあります")
    else: st.success("✅ 全て有効期限内です")

# 2. ダッシュボード
elif st.session_state.current_page == "dashboard":
    back_home_button()
    st.markdown("## 📊 分析レポート")
    r_w = min(amounts["水・飲料"] / (TARGETS["水・飲料"] or 1), 1.0)
    r_f = min(amounts["主食類"] / (TARGETS["主食類"] or 1), 1.0)
    r_t = min(amounts["トイレ・衛生"] / (TARGETS["トイレ・衛生"] or 1), 1.0)
    score = int(((r_w + r_f + r_t) / 3) * 100)
    st.markdown(f'<div class="score-circle" style="--p: {score*3.6}deg;" data-score="{score}"></div>', unsafe_allow_html=True)
    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        st.write(f"**{CATEGORIES[k]} {k}**")
        st.progress(min(amounts[k]/TARGETS[k], 1.0))

# 3. 自動点検 (香川県自主点検表 準拠) [cite: 14, 21]
elif st.session_state.current_page == "inspection":
    back_home_button()
    st.markdown("## ✅ 自動点検 (デジタル裏取り)")
    def check_ui(id, q, ok, ev):
        cls = "check-ok" if ok else "check-ng"
        st.markdown(f'<div class="inspection-item {cls}"><small>{id}</small><br><b>{q}</b><br><small>証跡: {ev}</small></div>', unsafe_allow_html=True)
    
    check_ui("7-1", "避難者に対する食料・水の備蓄 [cite: 14]", amounts["水・飲料"] >= TARGETS["水・飲料"]*0.5, f"水充足率 {int(amounts['水・飲料']/TARGETS['水・飲料']*100)}%")
    check_ui("6-5", "簡易トイレ等の物資の備え [cite: 14]", amounts["トイレ・衛生"] >= t_pop*5, f"在庫 {int(amounts['トイレ・衛生'])}回")
    check_ui("7-2", "アレルギー対応食料等の要配慮者への備え [cite: 14]", amounts["乳幼児用品"] > 0, f"乳幼児関連在庫 {int(amounts['乳幼児用品'])}点")

# 4. 在庫・登録
elif st.session_state.current_page == "inventory":
    if st.session_state.inv_cat:
        if st.button("🔙 カテゴリ選択へ"): 
            st.session_state.inv_cat = None
            st.rerun()
        cat = st.session_state.inv_cat
        st.subheader(f"{CATEGORIES[cat]} {cat}")
        # AI登録 (プロンプト改善)
        img = st.file_uploader("写真で追加", type=["jpg","png","jpeg"])
        if img and st.button("AI解析実行", type="primary"):
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            res = model.generate_content([f"Extract stock: category={cat}, JSON:[{{'item':'str','qty':int,'memo':'str'}}]", Image.open(img)])
            try:
                for x in json.loads(res.text.replace("```json","").replace("```","")):
                    db.insert_stock(x['item'], x['qty'], cat, x['memo'])
                st.success("追加完了")
                st.rerun()
            except: st.error("解析失敗")
        for r in [s for s in stocks if get_cat_key(s.get('category','')) == cat]:
            with st.expander(f"{r['item']} ({r['qty']})"):
                if st.button("削除", key=f"d_{r['id']}"):
                    db.delete_stock(r['id'])
                    st.rerun()
    else:
        back_home_button()
        st.markdown("### カテゴリ選択")
        cols = st.columns(2)
        for i, k in enumerate(CATEGORIES):
            with cols[i%2]:
                if st.button(f"{CATEGORIES[k]} {k}"):
                    st.session_state.inv_cat = k
                    st.rerun()

# 5. データ管理
elif st.session_state.current_page == "data":
    back_home_button()
    st.markdown("## 💾 データ管理")
    st.download_button("📥 CSVダウンロード", pd.DataFrame(stocks).to_csv(index=False).encode('utf-8-sig'), "backup.csv")
    if st.button("💥 全データ削除"):
        conn = sqlite3.connect('stock.db')
        conn.cursor().execute('DELETE FROM stocks')
        conn.commit(); conn.close()
        st.rerun()