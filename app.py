"""
香川防災DX - 本番システム
備蓄品の撮影・AI解析・永続化・一覧・エクスポート
"""
import os
import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import io
from datetime import datetime

import db

# --- 設定（本番は環境変数から） ---
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

st.set_page_config(page_title="香川防災DX", layout="centered")

if not GEMINI_API_KEY or "AIza" not in GEMINI_API_KEY:
    st.error("⚠️ **APIキーが設定されていません。** 環境変数 `GEMINI_API_KEY` を設定するか、プロジェクト直下に `.env` を作成し `GEMINI_API_KEY=あなたのキー` を記述してください。")
    st.stop()

try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
except Exception as e:
    st.error(f"APIキーの設定に失敗しました: {e}")
    st.stop()

# DB初期化
db.init_db()

# --- デザイン ---
st.markdown("""
    <style>
    div.stButton > button:first-child {
        font-size: 24px !important;
        font-weight: bold !important;
        height: 70px !important;
        width: 100% !important;
        background-color: #0066cc !important;
        color: white !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .stAlert { font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

st.title("⛑️ 香川防災DX")
st.caption("備蓄品管理システム（本番）")

if "captured_image_bytes" not in st.session_state:
    st.session_state.captured_image_bytes = None

tab1, tab2, tab3 = st.tabs(["📸 備蓄品を撮影", "📋 リストを見る", "⚙️ エクスポート"])

# ========== タブ1: 撮影・解析・登録 ==========
with tab1:
    st.write("### 📦 備蓄品を撮影してください")
    st.info("iPhoneの方は「写真をアップロード」→「写真を撮る」がおすすめです")

    img_file = st.file_uploader("📂 写真をアップロード", type=["jpg", "png", "jpeg", "heic"])
    img_cam = st.camera_input("📸 カメラで撮影")
    target_img = img_file if img_file else img_cam

    if target_img:
        st.session_state.captured_image_bytes = target_img.getvalue()

    if st.session_state.captured_image_bytes:
        image = Image.open(io.BytesIO(st.session_state.captured_image_bytes))
        st.image(image, caption="この画像を分析します", use_column_width=True)

        col_btn, col_clear = st.columns([1, 1])
        with col_btn:
            analyze_clicked = st.button("🔍 この写真を分析する")
        with col_clear:
            if st.button("🔄 新しい写真を撮る"):
                st.session_state.captured_image_bytes = None
                st.rerun()

        if analyze_clicked:
            with st.spinner("🤖 AIが画像を解析中... (数秒お待ちください)"):
                try:
                    prompt = """
                    この画像を丁寧に分析してください。
                    写っているものすべてを漏れなく特定し、それぞれの品名・数量・メモを出力してください。
                    ルール:
                    - 複数ある場合は必ずすべて列挙する
                    - 品名は具体的に（メーカー・型番が分かる場合は含める）
                    - 防災備蓄品以外も含めてすべて識別する
                    JSON形式のみで出力（配列）:
                    [{"item": "品名", "qty": "数量", "memo": "メモ"}, ...]
                    """
                    response = model.generate_content([prompt, image])
                    raw_text = response.text

                    filter_prompt = f"""
以下の読み取りデータから、「防災備蓄」として不適切な情報を完全に除外してください。
【除外】人物、背景、内装壁、扉、家具、PC周辺機器、スマートフォン・タブレット、装飾品など
【残す】衛生用品、食料・飲料、医療品、防災用品、簡易トイレ、毛布、電池、懐中電灯 など
残った備蓄対象品のみをJSON配列で出力。1件も該当しない場合は [] で出力。
フォーマット: [{{"item": "品名", "qty": "数量", "category": "カテゴリ", "memo": "備考"}}, ...]

【対象データ】
{raw_text}
"""
                    filter_response = model.generate_content(filter_prompt)
                    text = filter_response.text.replace("```json", "").replace("```", "").strip()
                    parsed = json.loads(text)
                    items = parsed if isinstance(parsed, list) else [parsed]

                    st.toast("分析完了！", icon="✅")
                    st.success(f"✅ 読み取り成功！（備蓄品{len(items)}件を検出）")

                    if not items:
                        st.info("📋 備蓄対象品は検出されませんでした。")
                    else:
                        for data in items:
                            item = data.get("item", data.get("品名", "-"))
                            qty = data.get("qty", data.get("数量", "-"))
                            category = data.get("category", data.get("カテゴリ", ""))
                            memo = data.get("memo", data.get("備考", data.get("メモ", "")))
                            db.insert_stock(item=item, qty=qty, category=category, memo=memo)
                            col1, col2 = st.columns([2, 1])
                            with col1:
                                st.markdown(f"**{item}**  _{category}_")
                            with col2:
                                st.markdown(qty)
                            if memo and memo != "-":
                                st.caption(f"📝 {memo}")
                        st.info("📋 「リストを見る」タブで登録内容を確認できます。データはサーバーに保存されています。")
                    st.session_state.captured_image_bytes = None

                except json.JSONDecodeError:
                    st.error("❌ AIの回答が読み取れませんでした。もう一度試してください。")
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: {e}")

# ========== タブ2: 一覧（DBから取得） ==========
with tab2:
    st.write("### 📋 登録済みリスト")
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        df_display = df[["item", "qty", "category", "memo", "created_at"]].copy()
        df_display.columns = ["品名", "数量", "カテゴリ", "備考", "登録日時"]
        st.dataframe(df_display, use_container_width=True)
    else:
        st.write("まだデータがありません。写真を撮って分析・登録してください。")

# ========== タブ3: エクスポート ==========
with tab3:
    st.write("### ⚙️ データのエクスポート")
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        df_export = df[["item", "qty", "category", "memo", "created_at"]].copy()
        df_export.columns = ["品名", "数量", "カテゴリ", "備考", "登録日時"]
        csv = df_export.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 CSVをダウンロード",
            data=csv,
            file_name=f"bousai_stock_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.info("登録データがありません。")
