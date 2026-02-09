"""
香川防災DX - 本番システム（モバイルファースト）
撮影→AI解析→確認フォーム→登録／カテゴリ別表示／データ管理
"""
import os
import re
import html
import csv
import io
from datetime import datetime, date, timedelta

import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd

import db
from db import CATEGORIES, STATUSES

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
    initial_sidebar_state="collapsed",
)

if not GEMINI_API_KEY or "AIza" not in GEMINI_API_KEY:
    st.error("⚠️ **APIキーが設定されていません。** 環境変数 `GEMINI_API_KEY` を設定するか、`.env` に `GEMINI_API_KEY=キー` を記述してください。")
    st.stop()

try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
except Exception as e:
    st.error(f"APIキーの設定に失敗しました: {e}")
    st.stop()

db.init_db()

# --- モバイルファースト用CSS（維持） ---
st.markdown("""
<style>
.block-container { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; padding-left: 0.75rem !important; padding-right: 0.75rem !important; max-width: 100% !important; }
.stTabs [data-baseweb="tab-list"] { gap: 0.25rem !important; }
.stTabs [data-baseweb="tab"] { padding: 0.5rem 0.75rem !important; font-size: 1rem !important; }
.stButton > button {
    font-size: 1.1rem !important; font-weight: bold !important; min-height: 48px !important; height: auto !important;
    padding: 0.75rem 1rem !important; width: 100% !important; background-color: #0066cc !important;
    color: white !important; border-radius: 12px !important; box-shadow: 0 2px 4px rgba(0,0,0,0.15);
}
.stCard { border: 1px solid #e0e0e0; border-radius: 12px; padding: 1rem; margin-bottom: 0.75rem; background: #fafafa; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.stCard.status-warn { background: #ffebee !important; border-color: #c62828 !important; }
.expiry-warn { color: #c62828 !important; font-weight: bold !important; }
.expiry-ok { color: #2e7d32 !important; }
.status-badge { font-weight: bold; padding: 0.2rem 0.5rem; border-radius: 6px; }
</style>
""", unsafe_allow_html=True)


def _parse_date(s: str) -> date | None:
    if not s or not str(s).strip():
        return None
    m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (ValueError, TypeError):
        return None


def _parse_expiry_from_memo(memo: str) -> tuple[str | None, bool]:
    if not memo or memo == "-":
        return None, False
    m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", memo)
    if not m:
        return None, False
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        expiry = date(y, mo, d)
        today = date.today()
        days = (expiry - today).days
        return f"{y}/{mo}/{d}", days < 0 or days <= 30
    except (ValueError, TypeError):
        return None, False


# セッション: 解析結果を保持し、フォームの初期値にする
if "captured_image_bytes" not in st.session_state:
    st.session_state.captured_image_bytes = None
if "parsed_item" not in st.session_state:
    st.session_state.parsed_item = None  # 1件分の辞書（確認フォーム用）

st.title("⛑️ 香川防災DX")
st.caption("備蓄品管理")

tab1, tab2, tab3, tab4 = st.tabs(["📸 撮影", "📋 在庫一覧", "📥 エクスポート", "🗃️ データ管理"])

# ========== タブ1: 撮影 → AI解析 → 確認・登録フォーム ==========
with tab1:
    st.markdown("#### 📷 撮影")
    img_cam = st.camera_input("カメラで撮影", key="cam")
    img_file = st.file_uploader("または写真をアップロード", type=["jpg", "png", "jpeg", "heic"], key="up")
    target_img = img_cam if img_cam else img_file

    if target_img:
        st.session_state.captured_image_bytes = target_img.getvalue()

    # 解析結果が1件ある場合: 確認・登録フォームを表示（撮影即保存はしない）
    parsed = st.session_state.get("parsed_item")
    if parsed is not None:
        st.markdown("##### 内容を確認して登録")
        # 初期値はAI結果。ユーザーが編集可能
        default_cat = parsed.get("category") or ""
        cat_index = next((i for i, c in enumerate(CATEGORIES) if c == default_cat), 0)
        form_item = st.text_input("品名", value=parsed.get("item", ""), key="form_item")
        form_qty = st.text_input("数量", value=parsed.get("qty", "1"), key="form_qty")
        form_category = st.selectbox("カテゴリ", CATEGORIES, index=cat_index, key="form_cat")
        form_memo = st.text_area("備考", value=parsed.get("memo", ""), key="form_memo")
        form_spec = st.text_input("スペック（W数・電圧など）", value=parsed.get("spec", ""), key="form_spec", placeholder="例: 定格1600W")
        form_status = st.selectbox("状態", STATUSES, index=STATUSES.index(parsed.get("status") or "稼働可") if (parsed.get("status") or "稼働可") in STATUSES else 0, key="form_status")
        maint_str = parsed.get("maintenance_date") or ""
        form_date_val = _parse_date(maint_str) or date.today()
        form_maintenance_date = st.date_input("点検日／賞味期限", value=form_date_val, key="form_date")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 登録する", type="primary", use_container_width=True, key="btn_register"):
                db.insert_stock(
                    item=form_item,
                    qty=form_qty,
                    category=form_category,
                    memo=form_memo,
                    status=form_status,
                    spec=form_spec,
                    maintenance_date=form_maintenance_date.strftime("%Y-%m-%d"),
                )
                st.session_state.parsed_item = None
                st.session_state.captured_image_bytes = None
                st.success("登録しました。")
                st.rerun()
        with col2:
            if st.button("🔄 やり直す", use_container_width=True, key="btn_cancel"):
                st.session_state.parsed_item = None
                st.session_state.captured_image_bytes = None
                st.rerun()
    elif st.session_state.captured_image_bytes:
        image = Image.open(io.BytesIO(st.session_state.captured_image_bytes))
        st.image(image, use_container_width=True)
        col_a, col_b = st.columns(2)
        with col_a:
            analyze_clicked = st.button("🔍 この写真を分析", type="primary", use_container_width=True)
        with col_b:
            if st.button("🔄 やり直す", use_container_width=True):
                st.session_state.captured_image_bytes = None
                st.session_state.parsed_item = None
                st.rerun()

        if analyze_clicked:
            with st.spinner("解析中..."):
                try:
                    prompt = """
この画像を分析し、防災備蓄として写っているものを1つ抽出してください。
資機材・設備の場合は点検票・銘板から「最終点検日」「スペック（W数・電圧など）」を、
食料の場合は「賞味期限」を読み取ってください。
破損・燃料不足などが分かれば状態を推奨してください。

JSON形式で1件のみ出力（配列にせずオブジェクト1つのみ）:
{"item": "品名", "qty": "数量", "category": "カテゴリ（主食類/副食等/水・飲料/乳幼児用品/衛生・トイレ/寝具・避難環境/資機材・重要設備のいずれか）", "memo": "備考", "maintenance_date": "YYYY-MM-DD（点検日または賞味期限）", "spec": "スペック", "status": "稼働可 or 修理中 or 要点検 or 期限切れ or 貸出中 or その他"}
"""
                    response = model.generate_content([prompt, image])
                    raw_text = response.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(raw_text)
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    # カテゴリを7つに寄せる
                    cat = (data.get("category") or "").strip()
                    if cat not in CATEGORIES:
                        data["category"] = "副食等"
                    st.session_state.parsed_item = data
                    st.success("解析しました。下記で内容を確認して登録してください。")
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("読み取れませんでした。もう一度試してください。")
                except Exception as e:
                    st.error(f"エラー: {e}")
    else:
        st.caption("上で撮影するか、写真をアップロードしてください。")

# ========== タブ2: 在庫一覧（カテゴリ別: 資機材は点検日・ステータスを目立たせる） ==========
with tab2:
    st.markdown("#### 📋 登録済み在庫")
    rows = db.get_all_stocks()
    if not rows:
        st.info("まだデータがありません。撮影タブで写真を撮って登録してください。")
    else:
        for r in rows:
            is_asset = (r.get("category") or "") == "資機材・重要設備"
            status = r.get("status") or "稼働可"
            is_warn_status = status not in ("稼働可", "")

            if is_asset:
                date_label = "点検日"
                date_val = r.get("maintenance_date") or "－"
            else:
                date_label = "賞味期限"
                memo_date, is_warn_exp = _parse_expiry_from_memo(r.get("memo") or "")
                date_val = r.get("maintenance_date") or memo_date or "－"
                is_warn_status = is_warn_status or (bool(memo_date and is_warn_exp))

            item_esc = html.escape(str(r["item"]))
            qty_esc = html.escape(str(r["qty"]))
            cat_esc = html.escape(str(r.get("category") or "－"))
            spec_esc = html.escape(str(r.get("spec") or "－"))
            card_class = "stCard"
            if is_warn_status:
                card_class += " status-warn"

            st.markdown(
                f'<div class="{card_class}">'
                f'<div style="font-weight:700; font-size:1.1rem;">{item_esc}</div>'
                f'<div style="color:#555;">数量: {qty_esc}　カテゴリ: {cat_esc}</div>'
                f'<div style="margin-top:0.35rem;">{date_label}: {html.escape(str(date_val))}'
                + (f'　仕様: {spec_esc}' if is_asset else '')
                + f'</div>'
                + (f'<div class="status-badge" style="margin-top:0.35rem; color:#c62828;">状態: {html.escape(status)}</div>' if is_asset else '')
                + '</div>',
                unsafe_allow_html=True,
            )

# ========== タブ3: エクスポート ==========
with tab3:
    st.markdown("#### 📥 CSVダウンロード")
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        cols = ["item", "qty", "category", "memo", "status", "spec", "maintenance_date", "created_at"]
        cols = [c for c in cols if c in df.columns]
        df_export = df[cols].copy()
        df_export.columns = ["品名", "数量", "カテゴリ", "備考", "状態", "仕様", "点検日/賞味期限", "登録日時"][:len(cols)]
        csv_data = df_export.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 CSVをダウンロード",
            data=csv_data,
            file_name=f"bousai_stock_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("登録データがありません。")

# ========== タブ4: データ管理（CSVインポート・エクスポート） ==========
with tab4:
    st.markdown("#### 🗃️ データ管理")
    st.markdown("##### CSV一括インポート")
    uploaded = st.file_uploader("CSVファイルをアップロード", type=["csv"], key="bulk_csv")
    if uploaded is not None:
        raw = uploaded.read()
        try:
            text = raw.decode("cp932")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8")
            except Exception:
                text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows_to_import = list(reader)
        if not rows_to_import:
            st.warning("CSVにデータ行がありません。")
        else:
            # カラム名のゆらぎ（日本語ヘッダ等）に対応
            def norm(r, *keys):
                for k in keys:
                    if k in r and r[k] is not None:
                        return str(r[k]).strip()
                    for header in r:
                        if header and str(header).strip() == str(k).strip():
                            return str(r.get(header, "") or "").strip()
                return ""

            normalized = []
            for row in rows_to_import:
                n = {
                    "item": norm(row, "item", "品名", "name") or "",
                    "qty": norm(row, "qty", "数量", "quantity") or "1",
                    "category": norm(row, "category", "カテゴリ") or "",
                    "memo": norm(row, "memo", "備考") or "",
                    "status": norm(row, "status", "状態") or "稼働可",
                    "spec": norm(row, "spec", "仕様") or "",
                    "maintenance_date": norm(row, "maintenance_date", "最終点検日", "賞味期限") or "",
                }
                if n["item"].strip():
                    normalized.append(n)
            count = db.bulk_insert_from_rows(normalized)
            st.success(f"✅ {count}件のデータを登録しました。")

    st.markdown("##### CSVエクスポート")
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        cols = [c for c in ["item", "qty", "category", "memo", "status", "spec", "maintenance_date", "created_at"] if c in df.columns]
        df_exp = df[cols].copy()
        df_exp.columns = ["品名", "数量", "カテゴリ", "備考", "状態", "仕様", "点検日/賞味期限", "登録日時"][:len(cols)]
        st.download_button(
            "📥 CSVをダウンロード",
            data=df_exp.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"bousai_stock_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            key="dl_manage",
            use_container_width=True,
        )
    else:
        st.info("登録データがありません。")
