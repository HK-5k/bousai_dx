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
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- 状態管理 ---
if 'current_page' not in st.session_state: st.session_state.current_page = "home"
if 'inv_cat' not in st.session_state: st.session_state.inv_cat = None

def navigate_to(page):
    st.session_state.current_page = page
    st.rerun()

# --- CSS (強制2列・中央配置) ---
st.markdown("""
<style>
.block-container { max-width: 600px !important; margin: 0 auto !important; }
h2 { text-align: center; }
[data-testid="stHorizontalBlock"] { display: flex !important; flex-direction: row !important; gap: 10px !important; }
[data-testid="stHorizontalBlock"] > div { width: 50% !important; min-width: 0px !important; }
div.stButton > button {
    width: 100% !important; height: 140px !important; background-color: white !important;
    border: 1px solid #ddd !important; border-radius: 20px !important;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08) !important; font-weight: bold !important;
    display: flex !important; flex-direction: column !important; justify-content: center !important;
}
.back-btn div.stButton > button { height: 50px !important; border-radius: 12px !important; background-color: #eee !important; }
.inspection-item { background: white; padding: 15px; border-radius: 12px; margin-bottom: 12px; border-left: 6px solid #ccc; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
.check-ok { border-left-color: #00c853 !important; }
.check-ng { border-left-color: #ff4b4b !important; }
</style>
""", unsafe_allow_html=True)

# --- ⚙️ 備蓄想定の設定 (サイドバー) ---
with st.sidebar:
    st.header("⚙️ 備蓄想定の設定")
    t_pop = st.number_input("想定対象人数 (人)", 10, 5000, 100, 10)
    t_days = st.slider("目標備蓄日数 (日)", 1, 7, 3)
    st.info(f"備蓄目標:\n**{t_pop}人 × {t_days}日分**")

# --- 定数と目標値 (香川県基準 [cite: 103]) ---
CATEGORIES = {"水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽", "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"}
TARGETS = {
    "水・飲料": t_pop * 3 * t_days,      # 3L/人/日
    "主食類": t_pop * 3 * t_days,        # 3食/人/日
    "トイレ・衛生": t_pop * 5 * t_days,  # 5回/人/日
}

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
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊\n分析レポート\n(充足率スコア)"): navigate_to("dashboard")
        if st.button("✅\n自動自主点検\n(○△×判定)"): navigate_to("inspection")
    with c2:
        if st.button("📦\n備蓄・登録\n(カテゴリ別)"): navigate_to("inventory")
        if st.button("💾\nデータ管理\n(CSV入出力)"): navigate_to("data")

    expired = [s for s in stocks if (d := re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(s.get('memo','')))) and datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))).date() < today]
    if expired: st.error(f"⚠️ {len(expired)}件の備蓄品が期限切れです！")
    else: st.success("✅ 全ての備蓄品が有効期限内です。")

# ==========================================
# 📊 分析レポート
# ==========================================
elif st.session_state.current_page == "dashboard":
    st.markdown('<div class="back-btn">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    st.subheader("📊 充足率レポート")
    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        pct = min(amounts[k]/TARGETS[k], 1.0) if TARGETS[k] > 0 else 0
        st.write(f"**{CATEGORIES[k]} {k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k])} / 目標: {TARGETS[k]} ({int(pct*100)}%)")

# ==========================================
# ✅ 自動自主点検 (デジタル裏取り [cite: 23, 67])
# ==========================================
elif st.session_state.current_page == "inspection":
    st.markdown('<div class="back-btn">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    st.subheader("✅ 自動自主点検")
    
    def check_ui(id, q, ok, ev):
        cls = "check-ok" if ok else "check-ng"
        st.markdown(f'<div class="inspection-item {cls}"><small>{id}</small><br><b>{q}</b><br><small>{"🟢 適合" if ok else "🔴 不適合"}: {ev}</small></div>', unsafe_allow_html=True)

    check_ui("7-1", "備蓄想定に対する食料・水の確保状況 [cite: 14]", (amounts["水・飲料"] >= TARGETS["水・飲料"]*0.5), f"水充足率 {int(amounts['水・飲料']/TARGETS['水・飲料']*100)}%")
    check_ui("6-5", "簡易トイレ等の物資の備え [cite: 14]", (amounts["トイレ・衛生"] >= t_pop*5), f"在庫 {int(amounts['トイレ・衛生'])}回")
    check_ui("7-2", "乳幼児・要配慮者への備え [cite: 14]", (amounts["乳幼児用品"] > 0), f"乳幼児用品在庫: {int(amounts['乳幼児用品'])}点")

# ==========================================
# 📦 備蓄・登録
# ==========================================
elif st.session_state.current_page == "inventory":
    if st.session_state.inv_cat:
        if st.button("🔙 カテゴリ選択へ"): 
            st.session_state.inv_cat = None
            st.rerun()
        cat = st.session_state.inv_cat
        st.subheader(f"{CATEGORIES[cat]} {cat}")
        img = st.file_uploader("写真で追加", type=["jpg","png","jpeg"])
        if img and st.button("解析して追加", type="primary"):
            with st.spinner("AI解析中..."):
                try:
                    p = f"Extract disaster stocks: category={cat}. JSON:[{{'item':'str','qty':int,'date':'YYYY-MM-DD','memo':'str'}}]"
                    res = model.generate_content([p, Image.open(img)])
                    data = json.loads(res.text.replace("```json","").replace("```","").strip())
                    for x in data: db.insert_stock(x.get('item','?'), x.get('qty',1), cat, x.get('memo',''))
                    st.success("完了"); time.sleep(1); st.rerun()
                except: st.error("エラー")
    else:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("🔙 ホームに戻る"): navigate_to("home")
        st.markdown('</div>', unsafe_allow_html=True)
        cols = st.columns(2)
        for i, k in enumerate(CATEGORIES):
            with cols[i%2]:
                if st.button(f"{CATEGORIES[k]} {k}"):
                    st.session_state.inv_cat = k
                    st.rerun()

# ==========================================
# 💾 データ管理
# ==========================================
elif st.session_state.current_page == "data":
    st.markdown('<div class="back-btn">', unsafe_allow_html=True)
    if st.button("🔙 ホームに戻る"): navigate_to("home")
    st.markdown('</div>', unsafe_allow_html=True)
    st.subheader("💾 データ管理")
    if st.download_button("📥 CSVダウンロード", pd.DataFrame(stocks).to_csv(index=False).encode('utf-8-sig'), "backup.csv"): pass
    if st.button("💥 全データ削除"):
        conn = sqlite3.connect('stock.db')
        conn.cursor().execute('DELETE FROM stocks')
        conn.commit(); conn.close()
        st.rerun()