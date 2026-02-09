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
# 🎨 デザイン刷新（モダンUI・DX仕様）
# ==========================================
st.markdown("""
<style>
/* 1. 全体の背景を「SaaS風」の薄いグレーに */
.stApp {
    background-color: #f4f6f9;
}

/* 2. 余白調整 */
.block-container { 
    padding-top: 2rem !important; 
    padding-bottom: 5rem !important; 
    max-width: 800px !important; /* スマホで見やすい幅に固定 */
}

/* 3. タイトルデザイン */
h1 {
    font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif;
    font-weight: 800 !important;
    color: #2c3e50;
    text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
    margin-bottom: 0.5rem !important;
}

/* 4. タブのデザイン */
.stTabs [data-baseweb="tab-list"] {
    background-color: #ffffff;
    padding: 10px 10px 0 10px;
    border-radius: 12px 12px 0 0;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.stTabs [data-baseweb="tab"] {
    height: 50px;
    font-weight: bold;
    color: #555;
}
.stTabs [aria-selected="true"] {
    color: #007bff !important;
    border-bottom-color: #007bff !important;
}

/* 5. カードデザイン（立体感・影） */
.stock-card {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1); /* ふんわりした影 */
    transition: transform 0.2s;
    border-left: 5px solid #ccc; /* デフォルト線 */
}
/* ホバー時に少し浮く */
.stock-card:active {
    transform: scale(0.98);
}

/* 6. ステータス別の色設定 */
.status-red { border-left-color: #ff4b4b !important; background-color: #fff5f5; }
.status-yellow { border-left-color: #ffa726 !important; background-color: #fffdf5; }
.status-green { border-left-color: #00c853 !important; }
.status-gray { border-left-color: #90a4ae !important; }

/* 7. バッジ（タグ）デザイン */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: bold;
    color: white;
    margin-bottom: 4px;
}
.badge-red { background-color: #ff4b4b; }
.badge-yellow { background-color: #ffa726; color: #fff !important; }
.badge-green { background-color: #00c853; }
.badge-gray { background-color: #90a4ae; }

/* 8. テキストスタイル */
.card-title {
    font-size: 1.1rem;
    font-weight: bold;
    color: #333;
    display: flex;
    align-items: center;
    gap: 8px;
}
.card-meta {
    font-size: 0.9rem;
    color: #666;
    margin-top: 4px;
}
.card-memo {
    font-size: 0.85rem;
    color: #888;
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed #eee;
}

/* 9. ボタンをリッチに */
.stButton > button {
    border-radius: 8px !important;
    font-weight: bold !important;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
}
</style>
""", unsafe_allow_html=True)

# APIチェック
if not GEMINI_API_KEY:
    st.error("⚠️ APIキーが設定されていません。")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# DB初期化
db.init_db()

# --- 日付解析ロジック ---
def extract_date(text):
    if not text: return None
    match = re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(text))
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except:
            return None
    return None

# --- ヘッダーエリア ---
col_h1, col_h2 = st.columns([1, 4])
with col_h2:
    st.markdown("""
    <div style="padding-top: 10px;">
        <h1 style="text-align: left; margin:0;">香川防災DX</h1>
        <p style="color: #666; font-size: 0.9rem; margin:0;">備蓄品在庫管理システム v2.0</p>
    </div>
    """, unsafe_allow_html=True)

# --- タブ ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 ダッシュボード", "📸 登録・撮影", "📋 在庫リスト", "⚙️ 設定・データ"])

# ========== 1. ダッシュボード（DX風） ==========
with tab1:
    stocks = db.get_all_stocks()
    
    if not stocks:
        st.info("ℹ️ データがありません。「📸 登録・撮影」タブから開始してください。")
        st.stop()

    # スタイリッシュな通知設定
    with st.expander("⚙️ アラート設定", expanded=False):
        alert_months = st.slider("期限切れ警告（ヶ月前）", 1, 24, 6)
    
    today = datetime.now().date()
    
    # 集計ロジック
    cnt_red = 0
    cnt_yellow = 0
    cnt_total = len(stocks)
    water_total = 0
    food_total = 0
    
    items_red = []
    items_yellow = []

    for s in stocks:
        # 水・食料計算
        try:
            qty = float(s.get('qty') or 0)
            cat = str(s.get('category') or "")
            if "水" in cat or "飲料" in cat: water_total += qty
            elif "主食" in cat or "副食" in cat: food_total += qty
        except: pass

        # 期限チェック
        exp_date = extract_date(s.get('memo', ''))
        item_info = {"品名": s['item'], "数量": s['qty'], "期限": exp_date}
        
        if exp_date:
            if exp_date < today:
                cnt_red += 1
                items_red.append(item_info)
            elif exp_date <= today + relativedelta(months=alert_months):
                cnt_yellow += 1
                items_yellow.append(item_info)

    # --- KPI カード表示 ---
    st.markdown("### Status Overview")
    kpi1, kpi2, kpi3 = st.columns(3)
    
    # デザインされたKPI
    def kpi_card(title, value, unit, color="#333"):
        return f"""
        <div style="background:white; padding:15px; border-radius:10px; box-shadow:0 2px 4px rgba(0,0,0,0.05); text-align:center;">
            <div style="font-size:0.8rem; color:#888;">{title}</div>
            <div style="font-size:1.8rem; font-weight:bold; color:{color};">{value}<span style="font-size:1rem; color:#aaa; margin-left:4px;">{unit}</span></div>
        </div>
        """
    
    with kpi1:
        st.markdown(kpi_card("登録アイテム", cnt_total, "件"), unsafe_allow_html=True)
    with kpi2:
        st.markdown(kpi_card("水（飲料）", int(water_total), "L", "#007bff"), unsafe_allow_html=True)
    with kpi3:
        st.markdown(kpi_card("食料", int(food_total), "食", "#ff9800"), unsafe_allow_html=True)
    
    st.markdown("---")

    # --- アクションリスト ---
    if cnt_red > 0:
        st.markdown(f"""
        <div style="background:#fff5f5; border-left:5px solid #ff4b4b; padding:15px; border-radius:4px; margin-bottom:15px;">
            <h4 style="margin:0; color:#c62828;">⚠️ 緊急対応が必要 ({cnt_red}件)</h4>
            <p style="margin:5px 0 0 0; font-size:0.9rem;">以下のアイテムは期限が切れています。廃棄または交換してください。</p>
        </div>
        """, unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(items_red), hide_index=True, use_container_width=True)
    
    if cnt_yellow > 0:
        st.markdown(f"""
        <div style="background:#fffdf5; border-left:5px solid #ffa726; padding:15px; border-radius:4px; margin-bottom:15px;">
            <h4 style="margin:0; color:#ef6c00;">📅 交換準備 ({cnt_yellow}件)</h4>
            <p style="margin:5px 0 0 0; font-size:0.9rem;">{alert_months}ヶ月以内に期限が切れます。</p>
        </div>
        """, unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(items_yellow), hide_index=True, use_container_width=True)

    if cnt_red == 0 and cnt_yellow == 0:
        st.success("✅ 全てのアラートはクリアされています。健全な管理状態です。")


# ========== 2. 登録・撮影 ==========
with tab2:
    st.markdown("#### 📷 新規アイテム登録")
    st.markdown("写真をアップロードすると、AIが自動で品名・数量・期限を読み取ります。")
    
    uploaded_file = st.file_uploader("", type=["jpg", "png", "jpeg", "heic"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="プレビュー", use_container_width=True)
        
        if st.button("✨ AI解析を実行する", type="primary", use_container_width=True):
            with st.spinner("AIが画像を分析中..."):
                try:
                    prompt = """
                    この画像を分析し、防災備蓄品データを抽出してください。
                    JSON配列形式: [{"item": "品名", "qty": 数値, "unit": "単位", "category": "カテゴリ", "date": "YYYY-MM-DD", "memo": "詳細"}]
                    【カテゴリ】1. 主食類, 2. 副食等, 3. 水・飲料, 4. 乳幼児用品, 5. 衛生・トイレ, 6. 寝具・避難環境, 7. 資機材・重要設備
                    ※賞味期限・使用期限(date)を全力で探してください。なければnull。
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
                    
                    st.success(f"完了: {count} 件を登録しました")
                    st.balloons()
                except Exception as e:
                    st.error(f"解析エラー: {e}")

# ========== 3. 在庫リスト（リッチデザイン） ==========
with tab3:
    col1, col2 = st.columns([3, 1])
    with col1:
        search_query = st.text_input("🔍 検索", placeholder="品名、メモから検索...")
    
    rows = db.get_all_stocks()
    if search_query:
        rows = [r for r in rows if search_query in str(r['item']) or search_query in str(r['memo'])]
    
    if not rows:
        st.info("表示するデータがありません。")
    
    alert_months_list = 6 
    today = datetime.now().date()
    
    st.markdown("---")

    for row in rows:
        stock_id = row['id']
        memo_str = str(row['memo'])
        exp_date = extract_date(memo_str)
        
        # デザインロジック
        status_class = "status-gray"
        badge_html = "<span class='badge badge-gray'>期限不明</span>"
        date_msg = "記載なし"
        
        if exp_date:
            days_left = (exp_date - today).days
            date_msg = f"{exp_date} ({days_left}日)"
            
            if days_left < 0:
                status_class = "status-red"
                badge_html = "<span class='badge badge-red'>期限切れ</span>"
            elif days_left <= (alert_months_list * 30):
                status_class = "status-yellow"
                badge_html = "<span class='badge badge-yellow'>交換推奨</span>"
            else:
                status_class = "status-green"
                badge_html = "<span class='badge badge-green'>安全</span>"

        # HTMLカード描画
        st.markdown(f"""
        <div class="stock-card {status_class}">
            <div style="display:flex; justify-content:space-between; align-items:start;">
                <div>
                    {badge_html}
                    <div class="card-title">{row['item']}</div>
                    <div class="card-meta">
                        📦 数量: <b>{row['qty']}</b> <span style="color:#ddd;">|</span> 📂 {row['category']}
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:0.8rem; color:#888;">期限</div>
                    <div style="font-weight:bold; color:#333;">{date_msg}</div>
                </div>
            </div>
            <div class="card-memo">{html.escape(memo_str)}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # アクション（編集・削除）
        with st.expander(f"🔧 操作 (ID: {stock_id})"):
            new_qty = st.number_input("数量変更", value=int(row['qty'] or 0), key=f"qty_{stock_id}")
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                if st.button("更新する", key=f"upd_{stock_id}"):
                    db.update_stock(stock_id, qty=new_qty)
                    st.success("更新しました")
                    st.rerun()
            with c_btn2:
                if st.button("削除する", key=f"del_{stock_id}", type="primary"):
                    db.delete_stock(stock_id)
                    st.rerun()

# ========== 4. データ管理 ==========
with tab4:
    st.markdown("#### 📥 📤 データの入出力")
    
    # 危険エリア
    st.markdown("##### ⚠️ システム管理")
    with st.expander("初期化メニュー（取り扱い注意）"):
        st.warning("この操作を行うと、登録された全ての備蓄データが消去されます。")
        if st.button("💥 全データを完全消去してリセット", type="primary"):
            try:
                conn = sqlite3.connect('stock.db')
                c = conn.cursor()
                c.execute('DELETE FROM stocks')
                conn.commit()
                conn.close()
                st.success("リセット完了しました。")
                st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")

    st.markdown("---")
    
    # CSV機能
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 CSVバックアップをダウンロード", data=csv, file_name="kagawa_dx_backup.csv", mime="text/csv", use_container_width=True)
    
    st.markdown("##### CSVから復元・一括登録")
    up_csv = st.file_uploader("CSVファイルをドラッグ＆ドロップ", type=["csv"])
    if up_csv:
        if st.button("登録を実行", use_container_width=True):
            try:
                try: df_new = pd.read_csv(up_csv, encoding='shift-jis')
                except: df_new = pd.read_csv(up_csv, encoding='utf-8')
                
                count = 0
                for index, r in df_new.iterrows():
                    db.insert_stock(
                        item=str(r.get('item', r.get('品名', '不明'))),
                        qty=int(r.get('qty', r.get('数量', 0))),
                        category=str(r.get('category', r.get('カテゴリ', 'その他'))),
                        memo=str(r.get('memo', r.get('備考', '')))
                    )
                    count += 1
                st.success(f"{count} 件を一括登録しました")
            except Exception as e:
                st.error(f"エラー: {e}")