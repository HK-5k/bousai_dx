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

# モバイル設定
st.set_page_config(
    page_title="香川防災DX",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- セッション状態の管理（画面遷移用） ---
if 'page' not in st.session_state:
    st.session_state.page = 'home' # home または category_view
if 'selected_category' not in st.session_state:
    st.session_state.selected_category = None
if 'selected_icon' not in st.session_state:
    st.session_state.selected_icon = ""

def go_home():
    st.session_state.page = 'home'
    st.session_state.selected_category = None

def go_category(category, icon):
    st.session_state.page = 'category_view'
    st.session_state.selected_category = category
    st.session_state.selected_icon = icon

# ==========================================
# 🎨 デザインCSS（ボタンをカード風にする）
# ==========================================
st.markdown("""
<style>
.stApp { background-color: #f4f6f9; }
.block-container { 
    padding-top: 1rem !important; 
    padding-bottom: 5rem !important; 
    max-width: 800px !important; 
}
h1 {
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-weight: 800 !important;
    color: #2c3e50;
    margin-bottom: 0.5rem !important;
}

/* ボタンをカードのように大きくする魔法 */
.stButton > button {
    height: 100px !important;
    width: 100% !important;
    border-radius: 12px !important;
    border: 1px solid #ddd !important;
    background-color: white !important;
    box-shadow: 0 4px 6px rgba(0,0,0,0.05) !important;
    color: #333 !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    align-items: center !important;
    padding: 10px !important;
    transition: all 0.2s !important;
}
.stButton > button:active {
    transform: scale(0.98) !important;
    background-color: #eef !important;
}
/* ボタン内の文字スタイル調整（改行対応） */
.stButton > button p {
    font-size: 1.1rem !important;
    font-weight: bold !important;
    line-height: 1.4 !important;
}

/* 在庫リストのカードスタイル */
.stock-card {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    border-left: 5px solid #ccc;
}
.status-red { border-left-color: #ff4b4b !important; background-color: #fff5f5; }
.status-yellow { border-left-color: #ffa726 !important; background-color: #fffdf5; }
.status-green { border-left-color: #00c853 !important; }

/* バッジ */
.badge {
    display: inline-block; padding: 2px 6px; border-radius: 4px;
    font-size: 0.7rem; font-weight: bold; color: white; margin-bottom: 4px;
}
.badge-red { background-color: #ff4b4b; }
.badge-yellow { background-color: #ffa726; color: #fff !important; }
.badge-green { background-color: #00c853; }
.badge-gray { background-color: #90a4ae; }
</style>
""", unsafe_allow_html=True)

if not GEMINI_API_KEY:
    st.error("⚠️ APIキーが設定されていません。")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
db.init_db()

def extract_date(text):
    if not text: return None
    match = re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(text))
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except: return None
    return None

# --- ヘッダー ---
col_h1, col_h2 = st.columns([1, 4])
with col_h2:
    st.markdown("""
    <div style="padding-top: 5px;">
        <h1 style="text-align: left; margin:0; font-size:1.5rem;">香川防災DX</h1>
        <p style="color: #666; font-size: 0.8rem; margin:0;">備蓄品在庫管理システム v4.0</p>
    </div>
    """, unsafe_allow_html=True)

# ----------------------------------------------------
# 📌 ロジック分岐：ホーム画面 か カテゴリ詳細画面 か
# ----------------------------------------------------

stocks = db.get_all_stocks()
if stocks is None: stocks = []
today = datetime.now().date()

# カテゴリ定義
CATEGORIES = {
    "水・飲料": "💧",
    "主食類": "🍚", # 副食含む
    "トイレ・衛生": "🚽",
    "乳幼児用品": "👶",
    "寝具・避難": "🛏️",
    "資機材": "🔋",
    "その他": "📦"
}

# マッピングヘルパー
def get_cat_key(db_cat_str):
    for key in CATEGORIES.keys():
        if key in str(db_cat_str): return key
        # 特別対応
        if key == "主食類" and ("食" in str(db_cat_str)): return key
    return "その他"

# 集計処理
counts = {k: 0 for k in CATEGORIES.keys()}
amounts = {k: 0 for k in CATEGORIES.keys()} # 数量合計

for s in stocks:
    cat_str = str(s.get('category') or "")
    qty = float(s.get('qty') or 0)
    
    key = get_cat_key(cat_str)
    counts[key] += 1
    amounts[key] += qty


# ==========================================
# 🏠 1. ホーム画面（ダッシュボード）
# ==========================================
if st.session_state.page == 'home':
    st.markdown("### 📦 備蓄カテゴリ選択")
    st.info("👇 アイコンをタップすると、登録・確認画面へ移動します")

    # 2列グリッドでボタンを配置
    col1, col2 = st.columns(2)
    
    keys = list(CATEGORIES.keys())
    
    # 左列
    with col1:
        # 水
        k = "水・飲料"
        label = f"{CATEGORIES[k]} {k}\n{int(amounts[k])}L ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_water"):
            go_category(k, CATEGORIES[k])
            st.rerun()

        # トイレ
        k = "トイレ・衛生"
        label = f"{CATEGORIES[k]} {k}\n{int(amounts[k])}回 ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_toilet"):
            go_category(k, CATEGORIES[k])
            st.rerun()
            
        # 寝具
        k = "寝具・避難"
        label = f"{CATEGORIES[k]} {k}\n{int(amounts[k])}枚 ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_sleep"):
            go_category(k, CATEGORIES[k])
            st.rerun()

    # 右列
    with col2:
        # 食料
        k = "主食類"
        label = f"{CATEGORIES[k]} 食料全般\n{int(amounts[k])}食 ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_food"):
            go_category(k, CATEGORIES[k])
            st.rerun()

        # 乳幼児
        k = "乳幼児用品"
        label = f"{CATEGORIES[k]} {k}\n{int(amounts[k])}点 ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_baby"):
            go_category(k, CATEGORIES[k])
            st.rerun()

        # 資機材
        k = "資機材"
        label = f"{CATEGORIES[k]} {k}\n{int(amounts[k])}台 ({counts[k]}件)"
        if st.button(label, use_container_width=True, key="btn_tools"):
            go_category(k, CATEGORIES[k])
            st.rerun()

    # その他（全幅）
    k = "その他"
    label = f"{CATEGORIES[k]} その他 ({counts[k]}件)"
    if st.button(label, use_container_width=True, key="btn_other"):
        go_category(k, CATEGORIES[k])
        st.rerun()
        
    # 全体アクション
    st.markdown("---")
    with st.expander("⚙️ データ管理・初期化"):
        st.download_button("📥 CSVダウンロード", data=pd.DataFrame(stocks).to_csv(index=False).encode('utf-8-sig'), file_name="backup.csv", mime="text/csv")
        if st.checkbox("全データを削除する"):
            if st.button("💥 削除実行", type="primary"):
                conn = sqlite3.connect('stock.db')
                conn.cursor().execute('DELETE FROM stocks')
                conn.commit()
                conn.close()
                st.success("削除しました")
                st.rerun()

# ==========================================
# 📂 2. カテゴリ詳細画面（登録 ＆ リスト）
# ==========================================
else:
    # 戻るボタン
    if st.button("🔙 ホームに戻る", type="secondary"):
        go_home()
        st.rerun()

    target_cat = st.session_state.selected_category
    target_icon = st.session_state.selected_icon
    
    st.markdown(f"## {target_icon} {target_cat} の管理")

    # --- A. 新規登録エリア ---
    st.markdown(f"### 📸 {target_cat}を追加")
    
    # タブ切り替え（撮影 vs 手入力）は不要、シンプルに
    uploaded_file = st.file_uploader(f"{target_cat}の写真を撮る", type=["jpg", "png", "jpeg", "heic"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, width=200)
        
        if st.button("✨ 解析して登録", type="primary", use_container_width=True):
            with st.spinner("AI解析中..."):
                try:
                    # カテゴリをヒントとして与えるプロンプト
                    prompt = f"""
                    この画像を分析し、防災備蓄品データを抽出してください。
                    特に「{target_cat}」に関連する情報を優先してください。
                    JSON配列: [{{"item": "品名", "qty": 数値, "unit": "単位", "date": "YYYY-MM-DD", "memo": "詳細"}}]
                    ※カテゴリは自動的に「{target_cat}」として扱います。
                    ※賞味期限・使用期限(date)を全力で探してください。
                    """
                    response = model.generate_content([prompt, image])
                    text = response.text.replace("```json", "").replace("```", "").strip()
                    items = json.loads(text)
                    
                    count = 0
                    for d in items:
                        memo_txt = d.get('memo', '')
                        date_txt = d.get('date')
                        if date_txt: memo_txt = f"{memo_txt} (期限: {date_txt})".strip()
                        
                        db.insert_stock(
                            item=d.get('item', '不明'),
                            qty=d.get('qty', 1),
                            category=target_cat, # 強制的に今のカテゴリで登録
                            memo=memo_txt
                        )
                        count += 1
                    st.success(f"{count}件 登録しました！")
                    st.rerun()
                except Exception as e:
                    st.error(f"エラー: {e}")

    st.markdown("---")

    # --- B. リストエリア（フィルタ済み） ---
    st.markdown(f"### 📋 {target_cat} リスト")
    
    # このカテゴリだけでフィルタリング
    filtered_stocks = [s for s in stocks if get_cat_key(s.get('category','')) == get_cat_key(target_cat)]
    
    if not filtered_stocks:
        st.info("データがありません。")
    
    for row in filtered_stocks:
        stock_id = row['id']
        memo_str = str(row['memo'])
        exp_date = extract_date(memo_str)
        
        status_class = "status-green"
        date_msg = "期限なし"
        
        if exp_date:
            days_left = (exp_date - today).days
            date_msg = f"{exp_date} (あと{days_left}日)"
            if days_left < 0: status_class = "status-red"
            elif days_left <= 180: status_class = "status-yellow"

        # HTMLカード表示
        st.markdown(f"""
        <div class="stock-card {status_class}">
            <div style="font-weight:bold; font-size:1.1rem;">{row['item']}</div>
            <div style="display:flex; justify-content:space-between; color:#555; font-size:0.9rem;">
                <div>数量: {row['qty']}</div>
                <div>{date_msg}</div>
            </div>
            <div style="font-size:0.8rem; color:#888;">{html.escape(memo_str)}</div>
        </div>
        """, unsafe_allow_html=True)
        
        with st.expander(f"編集・削除 (ID:{stock_id})"):
             n_qty = st.number_input("数量", value=int(row['qty']), key=f"q_{stock_id}")
             c1, c2 = st.columns(2)
             with c1:
                 if st.button("更新", key=f"up_{stock_id}"):
                     db.update_stock(stock_id, qty=n_qty)
                     st.rerun()
             with c2:
                 if st.button("削除", key=f"del_{stock_id}"):
                     db.delete_stock(stock_id)
                     st.rerun()