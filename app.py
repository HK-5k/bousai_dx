"""
香川防災DX - 本番システム（モバイルファースト）
撮影→AI解析→確認フォーム→登録／カテゴリ別表示／データ管理
"""
import os
import re
import html
import csv
import io
import uuid
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

# --- iPhone向けCSS（タイトル見切れ防止・上部余白） ---
st.markdown("""
<style>
h1 { font-size: clamp(1.5rem, 6vw, 2.2rem) !important; white-space: normal !important; word-wrap: break-word !important; line-height: 1.2 !important; }
.block-container { padding-top: 1.25rem !important; padding-bottom: 0.5rem !important; padding-left: 0.75rem !important; padding-right: 0.75rem !important; max-width: 100% !important; }
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
/* 編集・削除エクスパンダー内の2列目（削除ボタン）を赤くする */
[data-testid="stExpander"] [data-testid="column"]:last-child .stButton button { background-color: #c62828 !important; color: white !important; border-color: #c62828 !important; }
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


def _try_add_qty(a: str, b: str) -> str | None:
    """数量を数値として加算。両方パースできれば合計の文字列、否则 None。"""
    try:
        an = int(re.sub(r"[^0-9]", "", str(a)) or "0")
        bn = int(re.sub(r"[^0-9]", "", str(b)) or "0")
        return str(an + bn)
    except (ValueError, TypeError):
        return None


def _date_plus_years(d: date, years: int) -> date:
    """日付に年を加算（2/29は翌年がない場合は2/28に）。"""
    try:
        return date(d.year + years, d.month, d.day)
    except ValueError:
        return date(d.year + years, 2, 28)


def _pending_merge_key(p: dict) -> tuple:
    """カート合算用キー: (normalized name, due_type, due_date)。"""
    return (
        db.normalize_name(p.get("name") or p.get("item") or ""),
        (p.get("due_type") or "賞味期限").strip() or "賞味期限",
        (p.get("due_date") or "").strip(),
    )


def _cart_add_or_merge(pending_items: list, new_item: dict) -> list:
    """name + due_type + due_date が一致すれば数量加算、否则は末尾に追加。"""
    key = _pending_merge_key(new_item)
    name_norm = db.normalize_name(new_item.get("name") or new_item.get("item") or "")
    if not name_norm:
        return pending_items
    out = []
    merged = False
    for p in pending_items:
        pk = _pending_merge_key(p)
        if pk == key:
            qty_new = _try_add_qty(p.get("qty", "0"), new_item.get("qty", "1"))
            if qty_new is not None:
                out.append({**p, "qty": qty_new})
                merged = True
            else:
                out.append(p)
        else:
            out.append(p)
    if not merged:
        out.append(new_item)
    return out


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


# セッション: 解析結果・未登録カート（Pending: id, name, qty, due_type, due_date, memo, category, status, spec）
if "captured_image_bytes" not in st.session_state:
    st.session_state.captured_image_bytes = None
if "parsed_item" not in st.session_state:
    st.session_state.parsed_item = None
if "pending_items" not in st.session_state:
    st.session_state.pending_items = []
if "last_deleted_item" not in st.session_state:
    st.session_state.last_deleted_item = None

st.markdown("""
<h1 style='text-align: center; font-size: clamp(1.5rem, 6vw, 2.2rem); margin-bottom: 1rem; white-space: normal; word-wrap: break-word; line-height: 1.2;'>
    ⛑️ 香川防災DX<br><span style='font-size: 0.8em; color: gray;'>備蓄管理システム</span>
</h1>
""", unsafe_allow_html=True)

tab_summary, tab_camera, tab_list, tab_data = st.tabs(["📊 サマリー", "📸 撮影", "📋 在庫一覧", "💾 データ管理"])

# ========== タブ1: サマリー（備蓄状況・生存可能日数） ==========
with tab_summary:
    rows = db.get_all_stocks()
    if not rows:
        st.info("まだデータがありません。「📸 撮影」タブで写真を登録してください。")
    else:
        df = pd.DataFrame(rows)
        total = len(df)
        st.metric("登録品目数", f"{total} 品目")

        if "category" in df.columns:
            by_cat = df.groupby("category").size().sort_values(ascending=True)
            if not by_cat.empty:
                st.markdown("#### カテゴリ別内訳")
                st.bar_chart(by_cat.rename("件数"))

        # 生存可能日数の目安（水・主食・副食の有無から簡易表示）
        has_water = has_food = False
        if "category" in df.columns:
            water = df[df["category"].astype(str).str.contains("水", na=False)]
            food = df[df["category"].astype(str).str.contains("主食|副食", na=False, regex=True)]
            has_water = len(water) > 0
            has_food = len(food) > 0
        if has_water and has_food:
            st.metric("備蓄状況", "水・食料あり（生存可能日数は品目により異なります）")
        elif has_water:
            st.metric("備蓄状況", "水のみ登録（食料の登録を推奨）")
        elif has_food:
            st.metric("備蓄状況", "食料のみ登録（水の登録を推奨）")
        else:
            st.metric("備蓄状況", "水・食料を登録すると生存可能日数の目安を表示します")

# ========== タブ2: 写真選択 → AI解析 → 確認フォーム → リストに追加 or 登録 ==========
with tab_camera:
    img_file = st.file_uploader("📸 撮影 または 写真を選択", type=["jpg", "png", "jpeg", "heic"], key="up")
    target_img = img_file

    if target_img:
        st.session_state.captured_image_bytes = target_img.getvalue()

    parsed = st.session_state.get("parsed_item")
    pending_items = st.session_state.get("pending_items") or []

    if parsed is not None:
        # 日付ワンタップ用: session_state で日付を保持（callback で更新するため）
        if "form_date" not in st.session_state:
            st.session_state.form_date = _parse_date(parsed.get("maintenance_date") or "") or date.today()

        st.markdown("##### 内容を確認してから「リストに追加」または登録")
        default_cat = parsed.get("category") or ""
        cat_index = next((i for i, c in enumerate(CATEGORIES) if c == default_cat), 0)
        form_item = st.text_input("品名", value=parsed.get("item", ""), key="form_item")
        form_qty = st.text_input("数量", value=parsed.get("qty", "1"), key="form_qty")
        form_category = st.selectbox("カテゴリ", CATEGORIES, index=cat_index, key="form_cat")
        form_memo = st.text_area("備考", value=parsed.get("memo", ""), key="form_memo")
        form_spec = st.text_input("スペック（W数・電圧など）", value=parsed.get("spec", ""), key="form_spec", placeholder="例: 定格1600W")
        form_status = st.selectbox("状態", STATUSES, index=STATUSES.index(parsed.get("status") or "稼働可") if (parsed.get("status") or "稼働可") in STATUSES else 0, key="form_status")
        due_type = "点検日" if form_category == "資機材・重要設備" else "賞味期限"

        # 日付: session_state と連動（ワンタップボタンで callback が form_date を更新）
        form_maintenance_date = st.date_input("点検日／賞味期限", value=st.session_state.form_date, key="form_date")
        # ワンタップ [+1年][+3年][+5年]（on_click 内で st.session_state.form_date を直接更新）
        def make_add_years(years: int):
            def _add():
                d = st.session_state.get("form_date") or date.today()
                st.session_state.form_date = _date_plus_years(d, years)
            return _add

        bt1, bt2, bt3 = st.columns(3)
        with bt1:
            st.button("+1年", key="btn_y1", on_click=make_add_years(1), use_container_width=True)
        with bt2:
            st.button("+3年", key="btn_y3", on_click=make_add_years(3), use_container_width=True)
        with bt3:
            st.button("+5年", key="btn_y5", on_click=make_add_years(5), use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📋 リストに追加（一時保存）", type="primary", use_container_width=True, key="btn_add_to_cart"):
                one = {
                    "id": str(uuid.uuid4())[:8],
                    "name": form_item.strip(),
                    "qty": form_qty.strip() or "1",
                    "due_type": due_type,
                    "due_date": form_maintenance_date.strftime("%Y-%m-%d"),
                    "memo": form_memo.strip(),
                    "category": form_category,
                    "status": form_status,
                    "spec": form_spec.strip(),
                }
                st.session_state.pending_items = _cart_add_or_merge(pending_items, one)
                st.session_state.parsed_item = None
                if "form_date" in st.session_state:
                    del st.session_state.form_date
                st.toast("カートに追加しました。次の撮影へ。")
                st.rerun()
        with col2:
            if st.button("✅ この1件だけ登録する", use_container_width=True, key="btn_register_one"):
                db.insert_stock(
                    item=db.normalize_name(form_item) or form_item,
                    qty=form_qty,
                    category=form_category,
                    memo=form_memo,
                    status=form_status,
                    spec=form_spec,
                    maintenance_date=form_maintenance_date.strftime("%Y-%m-%d"),
                    due_type=due_type,
                )
                st.session_state.parsed_item = None
                st.session_state.captured_image_bytes = None
                st.success("登録しました。")
                st.rerun()
        if st.button("🔄 やり直す", use_container_width=True, key="btn_cancel"):
            st.session_state.parsed_item = None
            st.session_state.captured_image_bytes = None
            if "form_date" in st.session_state:
                del st.session_state.form_date
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
画像内に同じものが複数ある場合（ダンボールの山・複数棚など）、可能な限り総数を推定して qty に入れてください。
資機材の場合は点検票・銘板から「最終点検日」「スペック（W数・電圧など）」を、食料の場合は「賞味期限」を読み取ってください。
破損・燃料不足などが分かれば状態を推奨してください。

JSON形式で1件のみ出力（配列にせずオブジェクト1つのみ）:
{"item": "品名", "qty": "数量（複数ある場合は推定総数）", "category": "カテゴリ（主食類/副食等/水・飲料/乳幼児用品/衛生・トイレ/寝具・避難環境/資機材・重要設備のいずれか）", "memo": "備考", "maintenance_date": "YYYY-MM-DD", "spec": "スペック", "status": "稼働可 or 修理中 or 要点検 or 期限切れ or 貸出中 or その他"}
"""
                    response = model.generate_content([prompt, image])
                    raw_text = response.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(raw_text)
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    cat = (data.get("category") or "").strip()
                    if cat not in CATEGORIES:
                        data["category"] = "副食等"
                    st.session_state.parsed_item = data
                    st.success("解析しました。内容を確認して「リストに追加」または登録してください。")
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("読み取れませんでした。もう一度試してください。")
                except Exception as e:
                    st.error(f"エラー: {e}")
    else:
        pass  # ファーストビュー: タイトル＋アップロードのみ

    # 削除Undo（カートが空でも表示）
    if st.session_state.get("last_deleted_item") is not None:
        if st.button("↩️ 元に戻す", type="secondary", use_container_width=True, key="btn_undo"):
            st.session_state.pending_items = (st.session_state.pending_items or []) + [st.session_state.last_deleted_item]
            st.session_state.last_deleted_item = None
            st.toast("カートに戻しました。")
            st.rerun()

    # 未登録リスト（カート）: 最新1件を展開、要約ヘッダー
    if pending_items:
        st.markdown("---")
        st.markdown("#### 📋 未登録リスト（現在のカート）")

        # 最新が上（逆順）、先頭のみ expanded=True
        for idx, p in enumerate(reversed(pending_items)):
            name = p.get("name") or p.get("item") or ""
            qty = p.get("qty") or "1"
            due_type = p.get("due_type") or "賞味期限"
            due_date = (p.get("due_date") or "").strip()
            due_short = due_date[:7].replace("-", "/") if len(due_date) >= 7 else due_date
            header = f"【{name}】 {qty} ({due_type}: {due_short})"
            is_newest = idx == 0
            with st.expander(header, expanded=is_newest):
                st.caption(f"カテゴリ: {p.get('category', '')}　備考: {p.get('memo', '') or '－'}")
                if st.button("カートから削除", key=f"cart_del_{p.get('id', idx)}", type="secondary"):
                    st.session_state.last_deleted_item = p
                    st.session_state.pending_items = [x for x in pending_items if x.get("id") != p.get("id")]
                    st.toast("削除しました。「元に戻す」で復元できます。")
                    st.rerun()

        if st.button("✅ 全件まとめてDB登録", type="primary", use_container_width=True, key="btn_bulk_register"):
            payload = []
            for p in pending_items:
                name = (p.get("name") or p.get("item") or "").strip()
                if not name:
                    continue
                payload.append({
                    "name": name,
                    "qty": (p.get("qty") or "1").strip(),
                    "due_type": (p.get("due_type") or "賞味期限").strip() or "賞味期限",
                    "due_date": (p.get("due_date") or "").strip(),
                    "memo": (p.get("memo") or "").strip(),
                    "category": (p.get("category") or "").strip(),
                    "status": (p.get("status") or "稼働可").strip(),
                    "spec": (p.get("spec") or "").strip(),
                })
            logs, ok = db.bulk_register_with_merge(payload)
            if ok:
                st.session_state.pending_items = []
                st.session_state.last_deleted_item = None
                for msg in logs:
                    st.success(msg)
                st.rerun()
            else:
                st.error("登録中にエラーが発生しました。データは反映されていません。")

# ========== タブ3: 在庫一覧（カテゴリ別: 資機材は点検日・ステータスを目立たせる） ==========
with tab_list:
    st.markdown("#### 📋 登録済み在庫")
    rows = db.get_all_stocks()
    if not rows:
        st.info("まだデータがありません。撮影タブで写真を撮って登録してください。")
    else:
        for r in rows:
            sid = r.get("id")
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

            with st.expander("🔧 編集・削除", expanded=False):
                cat_idx = next((i for i, c in enumerate(CATEGORIES) if c == (r.get("category") or "")), 0)
                status_idx = next((i for i, s in enumerate(STATUSES) if s == (r.get("status") or "稼働可")), 0)
                edit_item = st.text_input("品名", value=r.get("item") or "", key=f"tab2_name_input_{sid}")
                edit_qty = st.text_input("数量", value=r.get("qty") or "1", key=f"tab2_qty_input_{sid}")
                edit_category = st.selectbox("カテゴリ", CATEGORIES, index=cat_idx, key=f"tab2_category_select_{sid}")
                edit_memo = st.text_area("備考", value=r.get("memo") or "", key=f"tab2_memo_input_{sid}")
                edit_spec = st.text_input("スペック", value=r.get("spec") or "", key=f"tab2_spec_input_{sid}")
                edit_status = st.selectbox("状態", STATUSES, index=status_idx, key=f"tab2_status_select_{sid}")
                edit_date_str = r.get("maintenance_date") or ""
                edit_date_val = _parse_date(edit_date_str) or date.today()
                edit_maintenance_date = st.date_input("点検日／賞味期限", value=edit_date_val, key=f"tab2_date_input_{sid}")

                if st.button("修正・保存", key=f"tab2_update_btn_{sid}", use_container_width=True, type="primary"):
                    db.update_stock(
                        sid,
                        item=edit_item,
                        qty=edit_qty,
                        category=edit_category,
                        memo=edit_memo,
                        status=edit_status,
                        spec=edit_spec,
                        maintenance_date=edit_maintenance_date.strftime("%Y-%m-%d"),
                    )
                    st.success("更新しました。")
                    st.rerun()

                del_confirm = st.checkbox("削除する場合はチェックしてください", key=f"tab2_del_confirm_{sid}")
                if del_confirm:
                    if st.button("🗑️ 削除", type="secondary", use_container_width=True, key=f"tab2_del_btn_{sid}"):
                        db.delete_stock(sid)
                        st.error("削除しました。")
                        st.rerun()

# ========== タブ4: データ管理（CSVエクスポート・インポート統合） ==========
with tab_data:
    st.markdown("#### 💾 データ管理")

    st.markdown("##### 📥 CSVエクスポート")
    rows = db.get_all_stocks()
    if rows:
        df = pd.DataFrame(rows)
        cols = [c for c in ["item", "qty", "category", "memo", "status", "spec", "maintenance_date", "created_at"] if c in df.columns]
        df_export = df[cols].copy()
        df_export.columns = ["品名", "数量", "カテゴリ", "備考", "状態", "仕様", "点検日/賞味期限", "登録日時"][:len(cols)]
        st.download_button(
            label="📥 CSVをダウンロード",
            data=df_export.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"bousai_stock_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            key="dl_export",
            use_container_width=True,
        )
    else:
        st.info("登録データがありません。")

    st.markdown("##### 📤 CSV一括インポート")
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
