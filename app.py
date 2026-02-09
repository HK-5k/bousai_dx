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

# ==========================================
# 🎨 デザインCSS
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

/* --- グリッドレイアウト（スマホ2列固定） --- */
.kpi-grid-container {
    display: grid;
    grid-template-columns: repeat(2, 1fr); /* 2列強制 */
    gap: 12px;
    margin-bottom: 15px;
}

/* カードデザイン */
.kpi-card {
    background: white;
    padding: 12px 5px;
    border-radius: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    text-align: center;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    height: 110px;
    border: 1px solid #eee;
}
/* 4つ目のカード用の特別な色（ここで指定するからエラーにならない） */
.kpi-card.gray-bg {
    background-color: #f8f9fa;
    border: 1px dashed #ddd;
}

.kpi-icon { font-size: 1.8rem; margin-bottom: 5px; }
.kpi-label { font-size: 0.75rem; color: #888; font-weight: bold; }
.kpi-value { font-size: 1.2rem; font-weight: bold; color: #333; }
.kpi-unit { font-size: 0.8rem; color: #aaa; margin-left: 2px; }

/* 在庫カード */
.stock-card {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 12px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    border-left: 5px solid #ccc;
}
.status-red { border-left-color: #ff4b4b !important; background-color: #fff5f5; }
.status-yellow { border-left-color: #ffa726 !important; background-color: #fffdf5; }
.status-green { border-left-color: #00c853 !important; }
.status-gray { border-left-color: #90a4ae !important; }

/* バッジ */
.badge {
    display: inline-block; padding: 2px 6px; border-radius: 4px;
    font-size: 0.7rem; font-weight: bold; color: white; margin-bottom: 4px;
}
.badge-red { background-color: #ff4b4b; }
.badge-yellow { background-color: #ffa726; color: #fff !important; }
.badge-green { background-color: #00c853; }
.badge-gray { background-color: #90a4ae; }

.stButton > button {
    border-radius: 8px !important; font-weight: bold !important;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
}
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

# --- HTML生成関数（安全版） ---
def make_card(icon, label, value, unit, color="#333", extra_class=""):
    # インデントや改行を一切入れない1行の文字列にする（エラー回避の鉄則）
    return f"""<div class="kpi-card {extra_class}"><div class="kpi-icon">{icon}</div><div class="kpi-label">{label}</div><div class="kpi-value" style="color:{color}">{int(value)}<span class="kpi-unit">{unit}</span></div></div>"""

# --- ヘッダー ---
col_h1, col_h2 = st.columns([1, 4])
with col_h2:
    st.markdown("""
    <div style="padding-top: 5px;">
        <h1 style="text-align: left; margin:0; font-size:1.5rem;">香川防災DX</h1>
        <p style="color: #666; font-size: 0.8rem; margin:0;">備蓄品在庫管理システム v3.6</p>
    </div>
    """, unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["📊 サマリー", "📸 登録", "📋 在庫", "⚙️ 設定"])

# ========== 1. ダッシュボード ==========
with tab1:
    stocks = db.get_all_stocks()
    if stocks is None: stocks = []
    
    if not stocks:
        st.info("ℹ️ データがありません。「📸 登録」タブから開始してください。")
    else:
        today = datetime.now().date()
        
        # --- 集計 ---
        cnt_total = len(stocks)
        water_qty = 0
        food_qty = 0
        toilet_qty = 0
        baby_qty = 0
        sleep_qty = 0
        tools_qty = 0
        
        cnt_red = 0
        cnt_yellow = 0
        items_red = []
        items_yellow = []

        alert_months = 6

        for s in stocks:
            cat = str(s.get('category') or "")
            try:
                qty = float(s.get('qty') or 0)
            except:
                qty = 0.0
            
            if "水" in cat or "飲料" in cat: water_qty += qty
            elif "主食" in cat or "副食" in cat: food_qty += qty
            elif "トイレ" in cat or "衛生" in cat: toilet_qty += qty
            elif "乳幼児" in cat or "ミルク" in cat: baby_qty += qty
            elif "寝具" in cat or "避難" in cat or "毛布" in cat: sleep_qty += qty
            elif "資機材" in cat or "設備" in cat or "電池" in cat: tools_qty += qty

            exp_date = extract_date(s.get('memo', ''))
            item_info = {"品名": s['item'], "数量": s['qty'], "期限": exp_date}
            
            if exp_date:
                if exp_date < today:
                    cnt_red += 1
                    items_red.append(item_info)
                elif exp_date <= today + relativedelta(months=alert_months):
                    cnt_yellow += 1
                    items_yellow.append(item_info)

        # ----------------------------------------------------
        # 画面表示（ここが修正ポイント）
        # ----------------------------------------------------
        st.markdown("### 📦 備蓄状況")
        
        # 1. パーツを作る
        c1 = make_card("📊", "登録アイテム", cnt_total, "件")
        c2 = make_card("💧", "水・飲料", water_qty, "L", "#007bff")
        c3 = make_card("🍱", "食料", food_qty, "食", "#ff9800")
        # 4つ目：CSSクラス 'gray-bg' を使って色を変える（styleタグを使わない）
        c4 = make_card("📦", "その他", cnt_total, "件", color="#333", extra_class="gray-bg")
        
        # 2. 連結する（隙間なく）
        html_main = f"""<div class="kpi-grid-container">{c1}{c2}{c3}{c4}</div>"""
        
        # 3. 描画する
        st.markdown(html_main, unsafe_allow_html=True)
        
        
        st.markdown("### 🏥 生活・資機材")
        
        sc1 = make_card("🚽", "トイレ・衛生", toilet_qty, "回")
        sc2 = make_card("👶", "乳幼児用品", baby_qty, "点")
        sc3 = make_card("🛏️", "寝具・毛布", sleep_qty, "枚")
        sc4 = make_card("🔋", "資機材", tools_qty, "台")
        
        html_sub = f"""<div class="kpi-grid-container">{sc1}{sc2}{sc3}{sc4}</div>"""
        st.markdown(html_sub, unsafe_allow_html=True)

        # --- アラート ---
        if cnt_red > 0:
            st.markdown(f"""
            <div style="background:#fff5f5; border-left:5px solid #ff4b4b; padding:10px; border-radius:4px; margin-top:10px; margin-bottom:10px;">
                <strong style="color:#c62828;">⚠️ 期限切れ ({cnt_red}件)</strong>
            </div>
            """, unsafe_allow_html=True)
            if items_red:
                st.dataframe(pd.DataFrame(items_red), hide_index=True, use_container_width=True)
        
        if cnt_red == 0 and cnt_yellow == 0:
            st.success("✅ アラートなし（健全）")


# ========== 2. 登録 ==========
with tab2:
    st.markdown("#### 📷 新規登録")
    uploaded_file = st.file_uploader("写真をアップロード", type=["jpg", "png", "jpeg", "heic"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="プレビュー", use_container_width=True)
        
        if st.button("✨ AI解析・登録", type="primary", use_container_width=True):
            with st.spinner("解析中..."):
                try:
                    prompt = """
                    画像を分析し、防災備蓄品データを抽出してください。
                    JSON配列: [{"item": "品名", "qty": 数値, "unit": "単位", "category": "カテゴリ", "date": "YYYY-MM-DD", "memo": "詳細"}]
                    カテゴリ: 1.主食類, 2.副食等, 3.水・飲料, 4.乳幼児用品, 5.衛生・トイレ, 6.寝具・避難環境, 7.資機材・重要設備
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
                            category=d.get('category', '7. 資機材・重要設備'),
                            memo=memo_txt
                        )
                        count += 1
                    
                    st.success(f"{count}件 登録完了！")
                except Exception as e:
                    st.error(f"エラー: {e}")

# ========== 3. 在庫リスト ==========
with tab3:
    search_query = st.text_input("🔍 検索", placeholder="品名...")
    rows = db.get_all_stocks()
    if search_query:
        rows = [r for r in rows if search_query in str(r['item']) or search_query in str(r['category'])]
    
    if not rows: st.info("データなし")
    
    today = datetime.now().date()
    
    for row in rows:
        stock_id = row['id']
        memo_str = str(row['memo'])
        exp_date = extract_date(memo_str)
        
        status_class = "status-gray"
        badge_html = "<span class='badge badge-gray'>期限不明</span>"
        date_msg = "-"
        
        if exp_date:
            days_left = (exp_date - today).days
            date_msg = f"{exp_date} ({days_left}日)"
            if days_left < 0:
                status_class = "status-red"
                badge_html = "<span class='badge badge-red'>期限切れ</span>"
            elif days_left <= 180:
                status_class = "status-yellow"
                badge_html = "<span class='badge badge-yellow'>交換推奨</span>"
            else:
                status_class = "status-green"
                badge_html = "<span class='badge badge-green'>安全</span>"

        st.markdown(f"""
        <div class="stock-card {status_class}">
            <div style="display:flex; justify-content:space-between;">
                <div>
                    {badge_html}
                    <div style="font-weight:bold; font-size:1rem;">{row['item']}</div>
                    <div style="font-size:0.8rem; color:#666;">数量: <b>{row['qty']}</b> | {row['category']}</div>
                </div>
                <div style="text-align:right; font-size:0.8rem;">
                    <div style="color:#888;">期限</div>
                    <div style="font-weight:bold;">{date_msg}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        with st.expander(f"編集 ID:{stock_id}"):
            new_qty = st.number_input("数量", value=int(row['qty'] or 0), key=f"qty_{stock_id}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("更新", key=f"upd_{stock_id}"):
                    db.update_stock(stock_id, qty=new_qty)
                    st.rerun()
            with c2:
                if st.button("削除", key=f"del_{stock_id}"):
                    db.delete_stock(stock_id)
                    st.rerun()

# ========== 4. 設定 ==========
with tab4:
    st.markdown("#### ⚙️ データ管理")
    
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 CSVダウンロード", data=csv, file_name="backup.csv", mime="text/csv", use_container_width=True)

    st.markdown("---")
    
    with st.expander("⚠️ 初期化メニュー（管理者用）"):
        st.warning("登録データを全て削除します。")
        agree = st.checkbox("データを完全に削除することを理解しました")
        
        if agree:
            if st.button("💥 全データを削除実行", type="primary"):
                try:
                    conn = sqlite3.connect('stock.db')
                    c = conn.cursor()
                    c.execute('DELETE FROM stocks')
                    conn.commit()
                    conn.close()
                    st.success("削除しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"エラー: {e}")
        else:
            st.button("💥 全データを削除実行", disabled=True)