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

# CSS（スマホ完全対応・タイトル見切れ防止・ボタン調整）
st.markdown("""
<style>
/* 全体の余白を詰める */
.block-container { 
    padding-top: 1rem !important; 
    padding-bottom: 2rem !important; 
    padding-left: 0.5rem !important; 
    padding-right: 0.5rem !important; 
}
/* タイトル見切れ防止 */
h1 {
    font-size: clamp(1.5rem, 5vw, 2.2rem) !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    line-height: 1.3 !important;
    text-align: center;
}
/* ボタンをスマホで押しやすく */
.stButton > button {
    width: 100% !important;
    min-height: 50px !important;
    font-size: 1.1rem !important;
    border-radius: 12px !important;
    font-weight: bold !important;
}
/* タブの文字サイズ */
.stTabs [data-baseweb="tab"] {
    font-size: 1rem !important;
    padding: 0.5rem !important;
}
/* カードデザイン */
.stock-card {
    background-color: #f8f9fa;
    border: 1px solid #ddd;
    border-radius: 10px;
    padding: 15px;
    margin-bottom: 10px;
}
/* 期限切れ警告 */
.alert-expired { color: #d32f2f; font-weight: bold; }
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

# --- タイトル表示（HTMLで強制リサイズ） ---
st.markdown("""
<h1>⛑️ 香川防災DX<br><span style='font-size:0.7em; color:gray;'>備蓄管理システム</span></h1>
""", unsafe_allow_html=True)

# --- タブ構成 ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 サマリー", "📸 撮影", "📋 在庫一覧", "💾 データ"])

# ========== 1. サマリー（ダッシュボード） ==========
with tab1:
    st.markdown("### 備蓄状況サマリー")
    
    # 全データを取得して集計
    stocks = db.get_all_stocks()
    total_items = len(stocks)
    
    # 簡易計算（デモ用ロジック：水と食料の日数試算）
    water_total = sum([s['qty'] for s in stocks if "水" in s['category'] or "飲料" in s['category']])
    food_total = sum([s['qty'] for s in stocks if "主食" in s['category'] or "副食" in s['category']])
    
    # 想定人数（スライダー）
    people = st.slider("避難想定人数", 1, 100, 10)
    
    # 日数計算（水3L/人, 食料3食/人と仮定）
    days_water = round(water_total / (people * 3), 1) if water_total > 0 else 0
    days_food = round(food_total / (people * 3), 1) if food_total > 0 else 0

    # 大きな数字で表示（グラフ廃止）
    c1, c2 = st.columns(2)
    with c1:
        st.metric("💧 水の確保", f"{days_water} 日分", f"{water_total} L")
    with c2:
        st.metric("🍱 食料確保", f"{days_food} 日分", f"{food_total} 食")

    st.divider()
    
    # 期限切れチェック
    expired_count = 0
    today = datetime.now().date()
    for s in stocks:
        memo = s.get('memo', '')
        # 簡易的な日付抽出
        m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", memo)
        if m:
            try:
                exp_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
                if exp_date < today:
                    expired_count += 1
            except:
                pass
    
    if expired_count > 0:
        st.error(f"⚠️ 期限切れ・要点検のアイテムが {expired_count} 件あります！")
    else:
        st.success("✅ 期限切れのアイテムはありません。")

# ========== 2. 撮影（シンプル一本化） ==========
with tab2:
    st.markdown("### 新規登録")
    st.info("下のボタンをタップして、写真を撮ってください。")
    
    uploaded_file = st.file_uploader("📷 撮影 または 写真を選択", type=["jpg", "png", "jpeg", "heic"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="撮影画像", use_container_width=True)
        
        if st.button("🔍 この写真を分析して登録", type="primary"):
            with st.spinner("AIが解析中..."):
                try:
                    # 香川県基準カテゴリ
                    prompt = """
                    この画像を分析し、防災備蓄品データを抽出してください。
                    JSON配列形式: [{"item": "品名", "qty": 数値, "unit": "単位", "category": "カテゴリ", "date": "YYYY-MM-DD", "memo": "詳細"}]
                    
                    【カテゴリは以下から厳選】
                    1. 主食類, 2. 副食等, 3. 水・飲料, 4. 乳幼児用品, 
                    5. 衛生・トイレ, 6. 寝具・避難環境, 7. 資機材・重要設備
                    
                    ※消費期限や点検日が画像にあればdateに入れる。なければnull。
                    ※資機材（発電機など）の場合はスペックをmemoに入れる。
                    """
                    response = model.generate_content([prompt, image])
                    text = response.text.replace("```json", "").replace("```", "").strip()
                    items = json.loads(text)
                    
                    count = 0
                    for d in items:
                        # dateがnullなら今日から3年後を入れる（仮）
                        meme_txt = d.get('memo', '')
                        date_txt = d.get('date')
                        if date_txt:
                            meme_txt += f" (期限: {date_txt})"
                        else:
                            meme_txt += " (期限不明)"

                        db.insert_stock(
                            item=d.get('item', '不明'),
                            qty=d.get('qty', 1),
                            category=d.get('category', '7. 資機材・重要設備'),
                            memo=meme_txt
                        )
                        count += 1
                    
                    st.success(f"✅ {count} 件を登録しました！")
                    st.balloons()
                    
                except Exception as e:
                    st.error(f"解析エラー: {e}")

# ========== 3. 在庫一覧（編集・削除機能付き） ==========
with tab3:
    st.markdown("### 在庫リスト")
    
    # 検索機能
    search_query = st.text_input("🔍 検索（品名など）")
    
    rows = db.get_all_stocks()
    if search_query:
        rows = [r for r in rows if search_query in r['item'] or search_query in r['memo']]
        
    if not rows:
        st.info("データがありません。")
    
    for row in rows:
        stock_id = row['id']
        with st.container():
            st.markdown(f"""
            <div class="stock-card">
                <div style="font-weight:bold; font-size:1.2rem;">{row['item']}</div>
                <div style="color:#666;">数量: {row['qty']} | {row['category']}</div>
                <div style="font-size:0.9rem;">{row['memo']}</div>
            </div>
            """, unsafe_allow_html=True)
            
            # 編集・削除エリア
            with st.expander(f"🔧 編集・削除 (ID: {stock_id})"):
                new_qty = st.number_input("数量変更", value=int(row['qty']), key=f"qty_{stock_id}")
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("更新", key=f"upd_{stock_id}"):
                        db.update_stock(stock_id, qty=new_qty) # update関数が必要
                        st.success("更新しました")
                        st.rerun()
                with col_btn2:
                    if st.button("削除", key=f"del_{stock_id}", type="primary"):
                        db.delete_stock(stock_id)
                        st.error("削除しました")
                        st.rerun()

# ========== 4. データ管理（CSV） ==========
with tab4:
    st.markdown("### データ入出力")
    
    # CSVダウンロード
    df = pd.DataFrame(rows)
    if not df.empty:
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "📥 CSVエクスポート",
            data=csv,
            file_name="stock_backup.csv",
            mime="text/csv",
            use_container_width=True
        )
    
    st.divider()
    
    # CSVアップロード
    st.markdown("#### CSV一括登録")
    up_csv = st.file_uploader("CSVファイルをアップロード", type=["csv"])
    if up_csv:
        if st.button("一括登録を実行"):
            try:
                # shift-jis or utf-8
                try:
                    df_new = pd.read_csv(up_csv, encoding='shift-jis')
                except:
                    df_new = pd.read_csv(up_csv, encoding='utf-8')
                
                # DBへインサート（簡易実装）
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