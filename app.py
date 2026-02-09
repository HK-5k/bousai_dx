import os
import re
import html
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

# CSS（スマホ対応・カードデザイン・ステータス色）
st.markdown("""
<style>
.block-container { 
    padding-top: 1rem !important; 
    padding-bottom: 5rem !important; 
    padding-left: 0.5rem !important; 
    padding-right: 0.5rem !important; 
}
h1 {
    font-size: clamp(1.5rem, 5vw, 2.2rem) !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    line-height: 1.3 !important;
    text-align: center;
}
.stButton > button {
    width: 100% !important;
    min-height: 50px !important;
    font-size: 1.1rem !important;
    border-radius: 12px !important;
    font-weight: bold !important;
}
/* 在庫カードの基本スタイル */
.stock-card {
    background-color: #ffffff;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
/* ステータス別の左ボーダー */
.status-red { border-left: 6px solid #ff4b4b; }
.status-yellow { border-left: 6px solid #ffa726; }
.status-green { border-left: 6px solid #00c853; }

.card-title { font-weight: bold; font-size: 1.1rem; margin-bottom: 4px; }
.card-meta { color: #555; font-size: 0.9rem; margin-bottom: 4px; }
.card-date { font-weight: bold; font-size: 0.95rem; }
.text-red { color: #ff4b4b; }
.text-yellow { color: #e65100; }
.text-green { color: #2e7d32; }
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

# --- 日付解析ロジック（堅牢版） ---
def extract_date(text):
    """メモ等のテキストから日付を抽出する。見つからない場合はNone"""
    if not text:
        return None
    # YYYY-MM-DD, YYYY/MM/DD, YYYY年MM月DD日 などに対応
    match = re.search(r"(\d{4})[\/\-\年](\d{1,2})[\/\-\月](\d{1,2})", str(text))
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except:
            return None
    return None

# --- タイトル ---
st.markdown("""
<h1>⛑️ 香川防災DX<br><span style='font-size:0.7em; color:gray;'>在庫管理アクションセンター</span></h1>
""", unsafe_allow_html=True)

# --- タブ ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 管理ホーム", "📸 撮影", "📋 在庫一覧", "💾 データ"])

# ========== 1. 管理ホーム（アクションセンター） ==========
with tab1:
    # --- アラート設定 ---
    with st.expander("⚙️ 通知設定", expanded=False):
        alert_months = st.slider("賞味期限・点検期限の何ヶ月前に通知しますか？", 1, 24, 6)
    
    # 全データ取得
    stocks = db.get_all_stocks()
    today = datetime.now().date()
    
    # データを分類
    items_red = []    # 期限切れ
    items_yellow = [] # 期限間近
    items_green = []  # 安全
    items_unknown = [] # 日付なし
    
    for s in stocks:
        exp_date = extract_date(s.get('memo', ''))
        
        # 表示用の辞書を作成
        item_data = {
            "ID": s['id'],
            "品名": s['item'],
            "数量": s['qty'],
            "期限": exp_date,
            "保管場所": s.get('category', '-') # カテゴリを仮置き
        }
        
        if exp_date:
            if exp_date < today:
                items_red.append(item_data)
            elif exp_date <= today + relativedelta(months=alert_months):
                items_yellow.append(item_data)
            else:
                items_green.append(item_data)
        else:
            items_unknown.append(item_data)

    st.markdown("### 🔥 アクションセンター（今やるべきこと）")

    # 🔴 期限切れ（最優先）
    if items_red:
        st.error(f"⚠️ **【緊急】期限切れが {len(items_red)} 件あります！**\n\n直ちに廃棄または交換してください。")
        df_red = pd.DataFrame(items_red)
        st.dataframe(df_red[["品名", "数量", "期限"]], hide_index=True, use_container_width=True)
    
    # 🟡 期限間近（注意）
    if items_yellow:
        st.warning(f"📅 **【注意】{alert_months}ヶ月以内に切れる在庫が {len(items_yellow)} 件あります。**\n\n優先的に消費するか、買い替えを検討してください。")
        df_yellow = pd.DataFrame(items_yellow)
        # 期限が近い順にソート
        df_yellow = df_yellow.sort_values('期限')
        st.dataframe(df_yellow[["品名", "数量", "期限"]], hide_index=True, use_container_width=True)

    # 🟢 正常
    if not items_red and not items_yellow:
        st.success("✅ **現在、緊急対応が必要な在庫はありません。** 素晴らしい管理状態です！")
    else:
        st.info(f"✅ 期限に余裕がある在庫: {len(items_green)} 件")

    st.divider()
    
    # --- 従来のサマリー（簡易版） ---
    st.markdown("#### 全体備蓄量サマリー")
    water_total = 0
    food_total = 0
    for s in stocks:
        try:
            qty = float(s.get('qty') or 0)
            cat = str(s.get('category') or "")
            if "水" in cat or "飲料" in cat: water_total += qty
            elif "主食" in cat or "副食" in cat: food_total += qty
        except: continue
        
    c1, c2 = st.columns(2)
    c1.metric("💧 水の総量", f"{int(water_total)} L")
    c2.metric("🍱 食料総量", f"{int(food_total)} 食")


# ========== 2. 撮影（シンプル版維持） ==========
with tab2:
    st.markdown("### 新規登録")
    st.info("下のボタンをタップして、写真を撮ってください。")
    
    uploaded_file = st.file_uploader("📷 撮影 または 写真を選択", type=["jpg", "png", "jpeg", "heic"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="撮影画像", use_container_width=True)
        
        if st.button("🔍 分析して登録", type="primary"):
            with st.spinner("AIが解析中..."):
                try:
                    # プロンプト（日付抽出強化）
                    prompt = """
                    この画像を分析し、防災備蓄品データを抽出してください。
                    JSON配列形式: [{"item": "品名", "qty": 数値, "unit": "単位", "category": "カテゴリ", "date": "YYYY-MM-DD", "memo": "詳細"}]
                    
                    【カテゴリ】1. 主食類, 2. 副食等, 3. 水・飲料, 4. 乳幼児用品, 5. 衛生・トイレ, 6. 寝具・避難環境, 7. 資機材・重要設備
                    
                    ※パッケージ等の賞味期限・使用期限(date)を全力で探してください。
                    ※なければnull。
                    """
                    response = model.generate_content([prompt, image])
                    text = response.text.replace("```json", "").replace("```", "").strip()
                    items = json.loads(text)
                    
                    count = 0
                    for d in items:
                        memo_txt = d.get('memo', '')
                        date_txt = d.get('date')
                        # 日付をメモに追記して保存（後で解析するため）
                        if date_txt:
                            memo_txt = f"{memo_txt} (期限: {date_txt})".strip()
                        
                        db.insert_stock(
                            item=d.get('item', '不明'),
                            qty=d.get('qty', 1),
                            category=d.get('category', '7. 資機材・重要設備'),
                            memo=memo_txt
                        )
                        count += 1
                    
                    st.success(f"✅ {count} 件を登録しました！")
                    st.balloons()
                except Exception as e:
                    st.error(f"解析エラー: {e}")

# ========== 3. 在庫一覧（視覚化強化版） ==========
with tab3:
    st.markdown("### 在庫リスト")
    
    search_query = st.text_input("🔍 検索（品名など）")
    
    rows = db.get_all_stocks()
    if search_query:
        rows = [r for r in rows if search_query in str(r['item']) or search_query in str(r['memo'])]
    
    if not rows:
        st.info("データがありません。")
    
    # 再計算用変数（デフォルト6ヶ月）
    alert_months_list = 6 
    
    today = datetime.now().date()
    
    for row in rows:
        stock_id = row['id']
        memo_str = str(row['memo'])
        exp_date = extract_date(memo_str)
        
        # ステータス判定とクラス付与
        status_class = ""
        status_icon = "✅"
        date_display = "期限不明"
        
        if exp_date:
            days_left = (exp_date - today).days
            if days_left < 0:
                status_class = "status-red"
                status_icon = "❌"
                date_display = f"<span class='text-red'>期限切れ ({abs(days_left)}日超過)</span>: {exp_date}"
            elif days_left <= (alert_months_list * 30):
                status_class = "status-yellow"
                status_icon = "⚠️"
                date_display = f"<span class='text-yellow'>あと {days_left} 日</span>: {exp_date}"
            else:
                status_class = "status-green"
                status_icon = "✅"
                date_display = f"<span class='text-green'>安全（残り{days_left}日）</span>: {exp_date}"
        else:
            # 日付なし
            status_class = "" 
            status_icon = "⚪️"
            date_display = "期限記載なし"

        # HTMLカード表示
        with st.container():
            st.markdown(f"""
            <div class="stock-card {status_class}">
                <div class="card-title">{status_icon} {row['item']}</div>
                <div class="card-meta">数量: <b>{row['qty']}</b> | {row['category']}</div>
                <div class="card-date">{date_display}</div>
                <div style="font-size:0.8rem; color:#888; margin-top:4px;">{html.escape(memo_str)}</div>
            </div>
            """, unsafe_allow_html=True)
            
            # 編集・削除
            with st.expander(f"🔧 編集・削除 (ID: {stock_id})"):
                new_qty = st.number_input("数量変更", value=int(row['qty'] or 0), key=f"qty_{stock_id}")
                
                # 日付修正用
                col_upd1, col_upd2 = st.columns(2)
                with col_upd1:
                    if st.button("更新", key=f"upd_{stock_id}"):
                        db.update_stock(stock_id, qty=new_qty)
                        st.success("更新しました")
                        st.rerun()
                with col_upd2:
                    if st.button("削除", key=f"del_{stock_id}", type="primary"):
                        db.delete_stock(stock_id)
                        st.error("削除しました")
                        st.rerun()

# ========== 4. データ管理 ==========
with tab4:
    st.markdown("### データ入出力")
    
    # エクスポート
    if rows:
        df = pd.DataFrame(rows)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 CSVエクスポート", data=csv, file_name="stock_backup.csv", mime="text/csv", use_container_width=True)
    
    st.divider()
    
    # インポート
    up_csv = st.file_uploader("CSV一括登録", type=["csv"])
    if up_csv:
        if st.button("一括登録を実行"):
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
                st.success(f"{count} 件を追加しました！")
            except Exception as e:
                st.error(f"エラー: {e}")