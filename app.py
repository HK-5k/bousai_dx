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
    initial_sidebar_state="collapsed", # サイドバーは隠す
)

# --- ページ状態管理 ---
if 'current_page' not in st.session_state:
    st.session_state.current_page = "home"

def navigate_to(page_name):
    st.session_state.current_page = page_name
    st.rerun()

# --- 定数定義 ---
TARGET_POPULATION = 100 
DAYS = 3 
TARGETS = {
    "水・飲料": TARGET_POPULATION * 3 * DAYS, 
    "主食類": TARGET_POPULATION * 3 * DAYS,   
    "トイレ・衛生": TARGET_POPULATION * 5 * DAYS, 
    "毛布": TARGET_POPULATION * 1,            
}
CATEGORIES = {
    "水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽",
    "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"
}

# --- CSS（スマホアプリ風にする魔法） ---
st.markdown("""
<style>
/* 全体の背景 */
.stApp { background-color: #f8f9fa; }
.block-container { padding-top: 1rem; max-width: 600px !important; } /* スマホ幅に最適化 */

/* タイトル */
h1, h2, h3 { 
    font-family: "Helvetica Neue", Arial, sans-serif; 
    color: #333; 
    font-weight: 800;
}

/* --- メニューカード（iPhoneアイコン風） --- */
.menu-card-btn {
    border: none !important;
    background: white !important;
    box-shadow: 0 4px 10px rgba(0,0,0,0.05) !important;
    border-radius: 20px !important;
    height: 140px !important;
    width: 100% !important;
    margin-bottom: 10px !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    align-items: center !important;
    transition: transform 0.1s !important;
}
.menu-card-btn:active {
    transform: scale(0.96) !important;
    background-color: #f0f0f0 !important;
}
/* メニュー内の文字 */
.menu-icon { font-size: 3rem; margin-bottom: 10px; }
.menu-title { font-size: 1.1rem; font-weight: bold; color: #333; }
.menu-desc { font-size: 0.8rem; color: #888; }

/* 戻るボタン */
.back-btn {
    border: none; background: transparent; color: #007bff; font-weight: bold; font-size: 1rem;
    margin-bottom: 10px; cursor: pointer;
}

/* KPIカード */
.kpi-card {
    background: white; padding: 15px; border-radius: 15px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05); text-align: center; margin-bottom: 10px;
}
.score-circle {
    width: 140px; height: 140px; border-radius: 50%;
    background: conic-gradient(#007bff var(--p), #eee 0deg);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 15px auto; font-size: 2.5rem; font-weight: bold; color: #007bff;
    position: relative;
    box-shadow: inset 0 0 20px rgba(0,0,0,0.05);
}
.score-circle::after { content: attr(data-score); position: absolute; }

/* 点検リスト */
.inspection-item {
    background: white; padding: 15px; border-radius: 12px;
    margin-bottom: 12px; border-left: 6px solid #ccc;
    box-shadow: 0 2px 4px rgba(0,0,0,0.03);
}
.check-ok { border-left-color: #00c853 !important; }
.check-ng { border-left-color: #ff4b4b !important; }

/* Streamlitのデフォルト要素を隠す */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

if not GEMINI_API_KEY:
    st.error("⚠️ APIキーが必要です")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
db.init_db()

# --- ヘルパー関数 ---
def extract_date(text):
    if not text: return None
    match = re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(text))
    if match:
        try: return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except: return None
    return None

def get_cat_key(db_cat_str):
    for key in CATEGORIES.keys():
        if key in str(db_cat_str): return key
        if key == "主食類" and ("食" in str(db_cat_str)): return key
    return "その他"

# --- データ取得 ---
stocks = db.get_all_stocks()
if stocks is None: stocks = []
today = datetime.now().date()
amounts = {k: 0 for k in CATEGORIES}
for s in stocks:
    k = get_cat_key(s.get('category',''))
    try: amounts[k] += float(s.get('qty', 0))
    except: pass

# ==========================================
# 🏠 0. ホーム画面 (メニューハブ)
# ==========================================
if st.session_state.current_page == "home":
    st.markdown("## ⛑️ 香川防災DX")
    st.markdown("<p style='color:#666; margin-top:-15px;'>在庫管理 & デジタル自主点検</p>", unsafe_allow_html=True)
    
    # --- グリッドメニュー ---
    c1, c2 = st.columns(2)
    
    with c1:
        # ダッシュボード
        st.markdown("""
        <button class="menu-card-btn">
            <div class="menu-icon">📊</div>
            <div class="menu-title">分析レポート</div>
            <div class="menu-desc">充足率・スコア</div>
        </button>
        """, unsafe_allow_html=True)
        if st.button("分析レポートを開く", key="nav_dashboard", use_container_width=True):
            navigate_to("dashboard")

        # 在庫管理
        st.markdown("""
        <button class="menu-card-btn">
            <div class="menu-icon">📦</div>
            <div class="menu-title">在庫・登録</div>
            <div class="menu-desc">写真で追加・編集</div>
        </button>
        """, unsafe_allow_html=True)
        if st.button("在庫・登録を開く", key="nav_inventory", use_container_width=True):
            navigate_to("inventory")

    with c2:
        # デジタル点検
        st.markdown("""
        <button class="menu-card-btn">
            <div class="menu-icon">✅</div>
            <div class="menu-title">自動点検</div>
            <div class="menu-desc">○△×判定</div>
        </button>
        """, unsafe_allow_html=True)
        if st.button("自動点検を開く", key="nav_check", use_container_width=True):
            navigate_to("inspection")

        # データ管理
        st.markdown("""
        <button class="menu-card-btn">
            <div class="menu-icon">💾</div>
            <div class="menu-title">データ管理</div>
            <div class="menu-desc">CSV入出力・削除</div>
        </button>
        """, unsafe_allow_html=True)
        if st.button("データ管理を開く", key="nav_data", use_container_width=True):
            navigate_to("data")

    # --- クイックステータス ---
    st.markdown("### 🔔 現在の状況")
    
    # 期限切れチェック
    expired_count = 0
    for s in stocks:
        d = extract_date(s.get('memo',''))
        if d and d < today: expired_count += 1
        
    if expired_count > 0:
        st.error(f"⚠️ **{expired_count}件** の備蓄品が期限切れです！")
    else:
        st.success("✅ 期限切れの備蓄品はありません。")

    st.info(f"現在の避難想定: **{TARGET_POPULATION}人** (3日分)")


# ==========================================
# 📊 1. ダッシュボード画面
# ==========================================
elif st.session_state.current_page == "dashboard":
    if st.button("🔙 ホームに戻る", key="back_dash"): navigate_to("home")
    
    st.markdown("## 📊 分析レポート")
    
    # スコア計算
    rate_water = min(amounts["水・飲料"] / TARGETS["水・飲料"], 1.0) * 100
    rate_food = min(amounts["主食類"] / TARGETS["主食類"], 1.0) * 100
    rate_toilet = min(amounts["トイレ・衛生"] / TARGETS["トイレ・衛生"], 1.0) * 100
    total_score = int((rate_water + rate_food + rate_toilet) / 3)
    
    color = '#00c853' if total_score > 80 else '#ffa726' if total_score > 50 else '#ff4b4b'
    
    st.markdown(f"""
    <div class="kpi-card">
        <div style="color:#666; margin-bottom:10px;">防災備蓄 総合スコア</div>
        <div class="score-circle" style="--p: {total_score * 3.6}deg; background: conic-gradient({color} {total_score}%, #eee 0deg);" data-score="{total_score}"></div>
        <div style="font-weight:bold;">目標達成率</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### カテゴリ別詳細")
    
    def kpi_bar(label, current, target, unit, icon):
        pct = min(current / target, 1.0)
        st.write(f"**{icon} {label}**")
        st.progress(pct)
        st.caption(f"{int(current)} / {target} {unit} ({int(pct*100)}%)")
        
    kpi_bar("飲料水", amounts["水・飲料"], TARGETS["水・飲料"], "L", "💧")
    kpi_bar("食料", amounts["主食類"], TARGETS["主食類"], "食", "🍚")
    kpi_bar("トイレ", amounts["トイレ・衛生"], TARGETS["トイレ・衛生"], "回", "🚽")


# ==========================================
# 📦 2. 在庫・登録画面
# ==========================================
elif st.session_state.current_page == "inventory":
    if st.button("🔙 ホームに戻る", key="back_inv"): navigate_to("home")
    st.markdown("## 📦 在庫・登録")
    
    # 状態管理
    if 'inv_cat' not in st.session_state: st.session_state.inv_cat = None

    if st.session_state.inv_cat:
        # 詳細モード
        cat = st.session_state.inv_cat
        if st.button("🔙 カテゴリ選択へ", type="secondary"):
            st.session_state.inv_cat = None
            st.rerun()
            
        st.markdown(f"### {CATEGORIES.get(cat,'')} {cat}")
        
        # 登録
        img = st.file_uploader("写真で追加", type=["jpg","png","jpeg"])
        if img and st.button("解析して追加", type="primary", use_container_width=True):
             with st.spinner("AI解析中..."):
                try:
                    p = f"防災備蓄品抽出。カテゴリ「{cat}」。JSON配列: [{{'item':'品名','qty':1,'date':'','memo':''}}]"
                    res = model.generate_content([p, Image.open(img)])
                    d = json.loads(res.text.replace("```json","").replace("```","").strip())
                    for x in d:
                        db.insert_stock(x.get('item','?'), x.get('qty',1), cat, x.get('memo',''))
                    st.success("追加しました")
                    time.sleep(1)
                    st.rerun()
                except: st.error("エラー")

        # リスト
        st.markdown("---")
        fs = [s for s in stocks if get_cat_key(s.get('category','')) == cat]
        if not fs: st.info("データなし")
        for r in fs:
            with st.expander(f"{r['item']} ({r['qty']})"):
                if st.button("削除", key=f"del_{r['id']}"):
                    db.delete_stock(r['id'])
                    st.rerun()
    else:
        # カテゴリ一覧
        cols = st.columns(2)
        for i, k in enumerate(CATEGORIES):
            with cols[i%2]:
                label = f"{CATEGORIES[k]} {k}\n({int(amounts[k])})"
                if st.button(label, key=f"cat_{k}", use_container_width=True):
                    st.session_state.inv_cat = k
                    st.rerun()


# ==========================================
# ✅ 3. 自動点検画面
# ==========================================
elif st.session_state.current_page == "inspection":
    if st.button("🔙 ホームに戻る", key="back_insp"): navigate_to("home")
    st.markdown("## ✅ 自動点検")
    
    def check_row(qid, q, func):
        ok, reason = func()
        cls = "check-ok" if ok else "check-ng"
        icon = "🟢 適合" if ok else "🔴 不適合"
        st.markdown(f"""
        <div class="inspection-item {cls}">
            <div style="font-size:0.8rem; color:#888;">{qid}</div>
            <div style="font-weight:bold; margin-bottom:5px;">{q}</div>
            <div style="font-size:0.9rem; background:#f9f9f9; padding:8px; border-radius:5px;">
                <b>{icon}</b>: {reason}
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ロジック
    def c1():
        r = amounts["水・飲料"]/TARGETS["水・飲料"]
        return (r > 0.5, f"充足率 {int(r*100)}%")
    
    def c2():
        q = amounts["トイレ・衛生"]
        return (q >= TARGET_POPULATION*5, f"在庫 {int(q)}回")

    check_row("7-1", "避難者に対する備蓄(水・食料)を行っているか", c1)
    check_row("6-5", "簡易トイレなどの備えがあるか", c2)


# ==========================================
# 💾 4. データ画面
# ==========================================
elif st.session_state.current_page == "data":
    if st.button("🔙 ホームに戻る", key="back_data"): navigate_to("home")
    st.markdown("## 💾 データ管理")
    
    st.download_button("📥 CSVバックアップ", pd.DataFrame(stocks).to_csv().encode('utf-8-sig'), "backup.csv", use_container_width=True)
    
    up = st.file_uploader("📤 CSV復元", type=["csv"])
    if up and st.button("復元実行", use_container_width=True):
        try:
            df = pd.read_csv(up)
            for _, r in df.iterrows():
                db.insert_stock(str(r.get('item','')), int(r.get('qty',0)), str(r.get('category','')), str(r.get('memo','')))
            st.success("復元完了")
        except: st.error("エラー")

    st.markdown("---")
    if st.button("💥 全データ削除 (初期化)", type="primary", use_container_width=True):
        conn = sqlite3.connect('stock.db')
        conn.cursor().execute('DELETE FROM stocks')
        conn.commit()
        conn.close()
        st.success("初期化しました")
        time.sleep(1)
        st.rerun()