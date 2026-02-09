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

# --- 定数定義（香川県モデル・100人避難所基準） ---
# ※本来はマスタ管理だが、デモ用に固定
TARGET_POPULATION = 100 # 人
DAYS = 3 # 日分

# 必要量の目安
TARGETS = {
    "水・飲料": TARGET_POPULATION * 3 * DAYS, # 3L/人/日 = 900L
    "主食類": TARGET_POPULATION * 3 * DAYS,   # 3食/人/日 = 900食
    "トイレ・衛生": TARGET_POPULATION * 5 * DAYS, # 5回/人/日 = 1500回
    "毛布": TARGET_POPULATION * 1,            # 1枚/人 = 100枚
}

CATEGORIES = {
    "水・飲料": "💧", "主食類": "🍚", "トイレ・衛生": "🚽",
    "乳幼児用品": "👶", "寝具・避難": "🛏️", "資機材": "🔋", "その他": "📦"
}

# --- CSS ---
st.markdown("""
<style>
.stApp { background-color: #f4f6f9; }
.block-container { padding-top: 1rem; max-width: 900px; }
h1, h2, h3 { color: #2c3e50; font-family: sans-serif; }

/* カードデザイン */
.kpi-card {
    background: white; padding: 15px; border-radius: 10px;
    box-shadow: 0 2px 5px rgba(0,0,0,0.05); text-align: center; margin-bottom: 10px;
    border: 1px solid #eee;
}
.score-circle {
    width: 120px; height: 120px; border-radius: 50%;
    background: conic-gradient(#007bff var(--p), #eee 0deg);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 10px auto; font-size: 2rem; font-weight: bold; color: #007bff;
    position: relative;
}
.score-circle::after {
    content: attr(data-score) "%"; position: absolute;
}
.inspection-row {
    background: white; padding: 15px; border-radius: 8px;
    margin-bottom: 10px; border-left: 5px solid #ccc;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.check-ok { border-left-color: #00c853 !important; } /* 緑 */
.check-ng { border-left-color: #ff4b4b !important; } /* 赤 */
.check-warn { border-left-color: #ffa726 !important; } /* 黄 */

/* ボタン */
.stButton > button {
    border-radius: 8px; font-weight: bold; border: 1px solid #ddd;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
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

# --- サイドバー ---
with st.sidebar:
    st.markdown("### ⛑️ 香川防災DX")
    page = st.radio("メニュー", ["📊 ダッシュボード (評価)", "✅ デジタル自主点検", "🏠 登録・在庫管理", "💾 データ入出力"])
    
    st.markdown("---")
    st.markdown("**避難所設定 (シミュレーション)**")
    st.info(f"避難想定: **{TARGET_POPULATION}人**\n\n備蓄目標: **{DAYS}日分**")

# --- データ取得 ---
stocks = db.get_all_stocks()
if stocks is None: stocks = []
today = datetime.now().date()

# 集計処理
amounts = {k: 0 for k in CATEGORIES}
for s in stocks:
    k = get_cat_key(s.get('category',''))
    try: amounts[k] += float(s.get('qty', 0))
    except: pass

# ==========================================
# 📊 ダッシュボード (評価スコア)
# ==========================================
if page == "📊 ダッシュボード (評価)":
    st.markdown("## 📊 防災備蓄 健全性スコア")
    st.markdown("現在の在庫量が、想定避難者数（100名×3日分）に対してどれくらい足りているかを判定します。")

    # --- スコア計算ロジック ---
    # 各重要項目の充足率(max 100%)の平均をとる
    
    # 1. 水
    rate_water = min(amounts["水・飲料"] / TARGETS["水・飲料"], 1.0) * 100
    # 2. 食料
    rate_food = min(amounts["主食類"] / TARGETS["主食類"], 1.0) * 100
    # 3. トイレ
    rate_toilet = min(amounts["トイレ・衛生"] / TARGETS["トイレ・衛生"], 1.0) * 100
    
    # 総合スコア (水・食料・トイレの平均)
    total_score = int((rate_water + rate_food + rate_toilet) / 3)
    
    # --- スコア表示 ---
    c1, c2 = st.columns([1, 2])
    
    with c1:
        # ドーナツチャート風表示（CSSで描画）
        st.markdown(f"""
        <div style="text-align:center;">
            <div class="score-circle" style="--p: {total_score * 3.6}deg; background: conic-gradient({ '#00c853' if total_score > 80 else '#ffa726' if total_score > 50 else '#ff4b4b' } {total_score}%, #eee 0deg);">
                <span style="font-size:2rem; color:#333;">{total_score}</span><span style="font-size:1rem;">点</span>
            </div>
            <div style="font-weight:bold; color:#666;">総合充足率</div>
        </div>
        """, unsafe_allow_html=True)
    
    with c2:
        st.write("") # スペース調整
        if total_score < 30:
            st.error("🚨 **危険水準です**\n\n生命維持に必要な水・食料が圧倒的に不足しています。直ちに調達が必要です。")
        elif total_score < 80:
            st.warning("⚠️ **注意水準です**\n\n一部の物資が不足しています。カテゴリごとの充足率を確認してください。")
        else:
            st.success("✅ **安全水準です**\n\n素晴らしい管理状態です。期限切れに注意して維持してください。")

    st.markdown("---")
    
    # --- 詳細パラメータ ---
    st.markdown("### 📉 カテゴリ別 達成状況")
    
    def progress_bar(label, current, target, unit, icon):
        pct = min(current / target, 1.0)
        st.write(f"**{icon} {label}**")
        st.progress(pct)
        st.markdown(f"<div style='text-align:right; margin-top:-10px; font-size:0.9rem;'>現在: <b>{int(current)}</b> / 目標: {target} {unit} ({int(pct*100)}%)</div>", unsafe_allow_html=True)
    
    progress_bar("飲料水 (3L/人/日)", amounts["水・飲料"], TARGETS["水・飲料"], "L", "💧")
    progress_bar("食料 (3食/人/日)", amounts["主食類"], TARGETS["主食類"], "食", "🍚")
    progress_bar("トイレ (5回/人/日)", amounts["トイレ・衛生"], TARGETS["トイレ・衛生"], "回", "🚽")
    
    st.markdown("---")
    st.info("※ このスコアは、自主点検（○△×）の「根拠データ」として使用されます。")


# ==========================================
# ✅ デジタル自主点検
# ==========================================
elif page == "✅ デジタル自主点検":
    st.markdown("## ✅ 市町防災対策 自主点検表")
    st.markdown("PDFの点検項目に基づき、在庫データから**「自動判定（デジタル裏取り）」**を行います。")
    
    # --- 点検ロジック関数 ---
    def render_check_item(id, question, condition_func):
        is_ok, evidence_text = condition_func()
        status_cls = "check-ok" if is_ok else "check-ng"
        icon = "🟢 適合 (○)" if is_ok else "🔴 不適合 (×)"
        
        st.markdown(f"""
        <div class="inspection-row {status_cls}">
            <div style="font-size:0.85rem; color:#888;">点検項目 {id}</div>
            <div style="font-weight:bold; margin-bottom:5px;">{question}</div>
            <div style="background:#f9f9f9; padding:8px; border-radius:4px; font-size:0.9rem;">
                <span style="font-weight:bold;">{icon}</span><br>
                <span style="color:#555;">根拠データ: {evidence_text}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # --- 判定ロジック定義 ---
    
    # 7-1: 避難者に対する備蓄を行っているか
    def check_7_1():
        # 水と食料が目標の50%以上あればOKとする（仮基準）
        water_rate = amounts["水・飲料"] / TARGETS["水・飲料"]
        food_rate = amounts["主食類"] / TARGETS["主食類"]
        if water_rate > 0.5 and food_rate > 0.5:
            return True, f"水充足率 {int(water_rate*100)}%, 食料充足率 {int(food_rate*100)}% (基準50%クリア)"
        else:
            return False, f"水充足率 {int(water_rate*100)}%, 食料充足率 {int(food_rate*100)}% (不足あり)"

    # 6-5: 簡易トイレなどの物資の備えがあるか
    def check_6_5():
        qty = amounts["トイレ・衛生"]
        if qty >= TARGET_POPULATION * 5: # 1日分以上あれば一旦OK
            return True, f"トイレ在庫 {int(qty)}回 (最低必要数 {TARGET_POPULATION*5}回をクリア)"
        else:
            return False, f"トイレ在庫 {int(qty)}回 (目標 {TARGETS['トイレ・衛生']}回に対し不足)"

    # 7-2: アレルギー対応食料・要配慮者への備え
    def check_7_2():
        # 「アレルギー」「ミルク」「おかゆ」などがメモに含まれているか、または乳幼児カテゴリがあるか
        baby_qty = amounts["乳幼児用品"]
        allergy_items = [s for s in stocks if "アレルギー" in str(s.get('memo','')) or "除去" in str(s.get('item',''))]
        
        if baby_qty > 0 or len(allergy_items) > 0:
            return True, f"乳幼児用品 {int(baby_qty)}点, アレルギー対応候補 {len(allergy_items)}品目"
        else:
            return False, "該当する備蓄品が見当たりません"

    # --- リスト描画 ---
    st.subheader("7. 備蓄対策について")
    render_check_item("7-1", "南海トラフ地震(最大クラス)を想定した避難所への避難者に対する備蓄を行っているか", check_7_1)
    render_check_item("7-2", "アレルギー対応食料等の要配慮者に対する備蓄を行っているか", check_7_2)
    
    st.subheader("6. 避難所運営について")
    render_check_item("6-5", "簡易トイレなどの物資の備えがあるか", check_6_5)

# ==========================================
# 🏠 登録・在庫管理 (旧ホーム)
# ==========================================
elif page == "🏠 登録・在庫管理":
    # 以前のカテゴリボタンスタイル
    st.markdown("### 📦 備蓄登録・リスト")
    
    # 状態管理
    if 'selected_cat' not in st.session_state: st.session_state.selected_cat = None
    
    if st.session_state.selected_cat:
        # 詳細画面
        cat = st.session_state.selected_cat
        if st.button("🔙 一覧に戻る"):
            st.session_state.selected_cat = None
            st.rerun()
            
        st.markdown(f"#### {CATEGORIES.get(cat,'')} {cat}")
        
        # 簡易登録
        up = st.file_uploader("写真で追加", type=["jpg","png","jpeg"])
        if up:
            if st.button("登録実行", type="primary"):
                # (簡易実装: AI省略で1件ダミー登録もどき、実際は前のコード同様AI呼ぶ)
                # 今回はコード量削減のためAI部分は共通化イメージ
                # ★実稼働用にAIコード復活
                image = Image.open(up)
                with st.spinner("AI解析..."):
                    try:
                        prompt = f"防災備蓄品抽出。カテゴリ「{cat}」。JSON配列: [{{'item':'品名','qty':1,'date':'','memo':''}}]"
                        res = model.generate_content([prompt, image])
                        txt = res.text.replace("```json","").replace("```","").strip()
                        data = json.loads(txt)
                        for d in data:
                            db.insert_stock(d.get('item','不明'), d.get('qty',1), cat, d.get('memo',''))
                        st.success("登録完了")
                        time.sleep(1)
                        st.rerun()
                    except: st.error("AI解析失敗")
        
        # リスト
        fs = [s for s in stocks if get_cat_key(s.get('category','')) == cat]
        for r in fs:
            st.markdown(f"<div class='stock-card'><b>{r['item']}</b> 数量:{r['qty']}</div>", unsafe_allow_html=True)
            if st.button(f"削除 {r['id']}", key=f"del_{r['id']}"):
                db.delete_stock(r['id'])
                st.rerun()
                
    else:
        # カテゴリ一覧
        cols = st.columns(2)
        for i, k in enumerate(CATEGORIES):
            with cols[i%2]:
                label = f"{CATEGORIES[k]} {k}\n({int(amounts[k])})"
                if st.button(label, use_container_width=True, key=k):
                    st.session_state.selected_cat = k
                    st.rerun()

# ==========================================
# 💾 データ入出力
# ==========================================
elif page == "💾 データ入出力":
    st.markdown("## 💾 バックアップ・復元")
    
    if st.download_button("📥 CSVダウンロード", data=pd.DataFrame(stocks).to_csv().encode('utf-8-sig'), file_name="backup.csv"):
        pass
        
    up = st.file_uploader("📤 CSVアップロード", type=["csv"])
    if up and st.button("取り込み"):
        try:
            df = pd.read_csv(up)
            for _, r in df.iterrows():
                db.insert_stock(str(r.get('item','')), int(r.get('qty',0)), str(r.get('category','')), str(r.get('memo','')))
            st.success("完了")
        except: st.error("エラー")
        
    with st.expander("危険: 全削除"):
        if st.button("💥 実行"):
            conn = sqlite3.connect('stock.db')
            conn.cursor().execute('DELETE FROM stocks')
            conn.commit()
            conn.close()
            st.success("削除済")
            st.rerun()