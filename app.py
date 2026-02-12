import os
import re
import json
import ast
import uuid
import io
import inspect
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

try:
    import google.generativeai as genai
except Exception:
    genai = None
import platform
from pathlib import Path

def _choose_data_dir() -> Path:
    """
    優先順位:
      1) BOUSAI_DATA_DIR が指定されていればそれ
      2) Linuxなら /var/lib/bousai_dx を試す（VPS本番想定）
      3) リポジトリ内 ./data（ローカル開発で安全）
      4) ~/.bousai_dx（ローカル開発で安全）
      5) 最終手段 /tmp/bousai_dx（永続ではない）
    """
    candidates = []

    env = (os.environ.get("BOUSAI_DATA_DIR") or "").strip()
    if env:
        candidates.append(Path(env).expanduser())

    if platform.system().lower() == "linux":
        candidates.append(Path("/var/lib/bousai_dx"))

    here = Path(__file__).resolve().parent
    candidates.append(here / "data")
    candidates.append(Path.home() / ".bousai_dx")

    last_perm_err = None
    for base in candidates:
        try:
            (base / "db").mkdir(parents=True, exist_ok=True)
            (base / "photos").mkdir(parents=True, exist_ok=True)
            return base
        except PermissionError as e:
            last_perm_err = e
            continue

    # 最終手段（永続ではない）
    base = Path("/tmp/bousai_dx")
    (base / "db").mkdir(parents=True, exist_ok=True)
    (base / "photos").mkdir(parents=True, exist_ok=True)
    return base

DATA_DIR = _choose_data_dir()

# ここで「確定した永続パス」を環境変数に固定
os.environ["BOUSAI_DATA_DIR"] = str(DATA_DIR)
os.environ["STOCK_DB_PATH"] = os.environ.get("STOCK_DB_PATH") or str(DATA_DIR / "db" / "stock.db")
os.environ["PHOTO_DIR"] = os.environ.get("PHOTO_DIR") or str(DATA_DIR / "photos")

import db

# =========================================================
# API keys from server environment (ENV_GEMINI は必ずここで定義・NameError 防止)
# =========================================================
ENV_GEMINI = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
ENV_OPENAI = (os.getenv("OPENAI_API_KEY") or "").strip()

# =========================================================
# App config
# =========================================================
APP_TITLE = "香川防災DX"
st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="collapsed")

# =========================================================
# Session state
# =========================================================
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss_init("api_key", ENV_GEMINI)
ss_init("openai_api_key", ENV_OPENAI)
ss_init("current_page", "home")
ss_init("inv_cat", None)
ss_init("pending_items", [])  # AI結果カート（未登録）
ss_init("ai_last_raw", "")    # デバッグ用：AI生出力
# If session already exists but empty, hydrate from env
if not st.session_state.get("api_key") and ENV_GEMINI:
    st.session_state["api_key"] = ENV_GEMINI

if "openai_api_key" in st.session_state and (not st.session_state.get("openai_api_key")) and ENV_OPENAI:
    st.session_state["openai_api_key"] = ENV_OPENAI

def navigate_to(page: str) -> None:
    st.session_state.current_page = page
    st.rerun()

# =========================================================
# UI helper
# =========================================================
_SUPPORTS_WIDTH = "width" in inspect.signature(st.button).parameters

def button_stretch(label: str, *, key: str, type: str = "secondary", **kwargs) -> bool:
    """ボタンを横幅いっぱいに広げる"""
    if _SUPPORTS_WIDTH:
        return st.button(label, key=key, type=type, width="stretch", **kwargs)
    return st.button(label, key=key, type=type, use_container_width=True, **kwargs)

# =========================================================
# Constants
# =========================================================
CATEGORIES: Dict[str, str] = {
    "水・飲料": "💧",
    "主食類": "🍚",
    "トイレ・衛生": "🚽",
    "乳幼児用品": "👶",
    "寝具・避難": "🛏️",
    "資機材": "🔋",
    "その他": "📦",
}
DUE_LABEL = {"expiry": "賞味期限", "inspection": "点検日", "none": "期限なし"}
TOILET_SUBTYPES = ["携帯トイレ", "組立トイレ", "仮設トイレ", "トイレ袋", "凝固剤", "その他"]

# =========================================================
# CSS（iPhoneノッチ + 反応しない問題対策）
# =========================================================
st.markdown(
    """
<style>
html { -webkit-text-size-adjust: 100%; }
.stApp { background-color: #f8fafc; }

/* ✅ 最優先：ノッチ（セーフエリア）対策
   5rem くらい押し下げる + safe-area も足す */
.block-container{
  max-width: 600px !important;
  margin: 0 auto !important;
  padding-top: calc(5rem + env(safe-area-inset-top)) !important;
  padding-bottom: calc(4rem + env(safe-area-inset-bottom)) !important;
  padding-left: 0.9rem !important;
  padding-right: 0.9rem !important;
}

/* タイトル */
h2{
  text-align:center;
  font-weight: 900;
  color: #0f172a;
  margin-top: 0 !important;
  margin-bottom: 1.2rem !important;
}

/* ✅ ボタンが押せない(透明レイヤー被り)対策：
   ボタンを前面へ */
div.stButton > button{
  position: relative !important;
  z-index: 9999 !important;
}

/* タイルボタン（tile_） */
div.stElementContainer[class*="st-key-tile_"] div.stButton>button,
div.element-container[class*="st-key-tile_"] div.stButton>button {
  width:100% !important;
  height:auto !important;
  min-height: clamp(135px, 26vw, 185px) !important;
  padding: clamp(16px, 4.2vw, 26px) !important;
  border-radius: 20px !important;
  border: 1px solid #cbd5e1 !important;
  background: #ffffff !important;
  box-shadow: 0 8px 20px rgba(15,23,42,0.10) !important;
  display:flex !important;
  flex-direction:column !important;
  align-items:center !important;
  justify-content:center !important;
  color:#0f172a !important;
}
div.stElementContainer[class*="st-key-tile_"] div.stButton>button *,
div.element-container[class*="st-key-tile_"] div.stButton>button * {
  font-size: clamp(16px, 4.8vw, 22px) !important;
  font-weight: 900 !important;
  line-height: 1.35 !important;
  white-space: pre-line !important;
  text-align: center !important;
  color:#0f172a !important;
}
div.stElementContainer[class*="st-key-tile_"] div.stButton>button:active,
div.element-container[class*="st-key-tile_"] div.stButton>button:active {
  transform: scale(0.96) !important;
  background: #f1f5f9 !important;
}

/* 戻るボタン（back_） */
div.stElementContainer[class*="st-key-back_"] div.stButton>button,
div.element-container[class*="st-key-back_"] div.stButton>button {
  width:100% !important;
  height:56px !important;
  border-radius: 14px !important;
  background: #e2e8f0 !important;
  border:none !important;
  color:#475569 !important;
  font-weight: 900 !important;
  z-index: 9999 !important;
}

/* カード */
.card{
  background:white;
  padding:1.1rem;
  border-radius:16px;
  box-shadow: 0 4px 10px rgba(0,0,0,0.05);
  margin-bottom: 16px;
  border-left: 8px solid #cbd5e1;
}
.card-ok{ border-left-color:#22c55e !important; }
.card-ng{ border-left-color:#ef4444 !important; }
.card-warn{ border-left-color:#f59e0b !important; }

#MainMenu {visibility:hidden;}
footer {visibility:hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# =========================================================
# Sidebar settings
# =========================================================
with st.sidebar:
    st.header("⚙️ 備蓄設定")
    t_pop = st.number_input("想定人数", 1, 1_000_000, 100, 100)
    t_days = st.slider("目標日数", 1, 7, 3)

    st.markdown("---")
    st.header("APIキー設定")

    # サーバーにキーがあるなら基本はそれを使う（空入力で上書きしない）
    if ENV_GEMINI:
        st.success("Gemini APIキー: サーバー設定済み")
        override = st.text_input(
            "Gemini APIキー（一時上書き・任意）",
            type="password",
            placeholder="空ならサーバー設定を使用",
        ).strip()
        st.session_state["api_key"] = override if override else ENV_GEMINI
    else:
        api_key = st.text_input(
            "Google AI StudioのAPIキー",
            type="password",
            placeholder="AIzaSy...",
            value=st.session_state.get("api_key", ""),
        ).strip()
        api_key = (api_key or "").strip()
        # 空入力で上書きしない（空なら既存/session/ENV を維持）
        st.session_state["api_key"] = (api_key or st.session_state.get("api_key") or ENV_GEMINI or "").strip()
        if not (api_key or ENV_GEMINI):
            st.warning("ここにAPIキーを入力するとAI登録が有効になります。")

    st.markdown("---")
    st.header("AIモデル")

    MODEL_CHOICES = {
        "⚡ 速い（Flash-Lite）": "gemini-2.5-flash-lite",
        "🧠 高精度（Pro）": "gemini-2.5-pro",
    }
    model_label = st.selectbox("使用モデル", list(MODEL_CHOICES.keys()), index=0)
    selected_model = MODEL_CHOICES[model_label]

    timeout_sec = st.slider("AIタイムアウト(秒)", 15, 180, 60, 5)

    st.caption("※ AIが“無限グルグル”する場合は REST transport が効くことがあります（下で自動適用）")

# 反映キー: サイドバー入力(一時上書き) > サーバー環境変数(恒久)
EFFECTIVE_GEMINI_KEY = (st.session_state.get("api_key") or ENV_GEMINI or "").strip()
if EFFECTIVE_GEMINI_KEY:
    st.session_state["api_key"] = EFFECTIVE_GEMINI_KEY

# Configure Gemini (REST transport)
if genai is not None and EFFECTIVE_GEMINI_KEY.startswith("AIza"):
    try:
        genai.configure(api_key=EFFECTIVE_GEMINI_KEY, transport="rest")
    except Exception:
        genai.configure(api_key=EFFECTIVE_GEMINI_KEY)

TARGETS = {
    "水・飲料": t_pop * 3 * t_days,
    "主食類": t_pop * 3 * t_days,
    "トイレ・衛生": t_pop * 5 * t_days,
}

# =========================================================
# DB & aggregation
# =========================================================
db.init_db()
stocks = db.get_all_stocks() or []
today = datetime.now().date()

def get_cat_key(c: Any) -> str:
    s = str(c or "")
    for k in CATEGORIES:
        if k in s:
            return k
    return "その他"

def iso_to_date(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).split("T")[0])
    except Exception:
        m = re.search(r"(\d{4})[\/\-\.\年](\d{1,2})[\/\-\.\月](\d{1,2})", str(s))
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None

amounts: Dict[str, float] = {k: 0.0 for k in CATEGORIES}
expired_count = 0

for s in stocks:
    cat = get_cat_key(s.get("category"))
    kind = str(s.get("item_kind", "stock") or "stock")
    qty = float(s.get("qty", 0) or 0)
    unit = str(s.get("unit") or "").strip()

    # 飲料水：設備能力(capacity)は合算しない（在庫のみ）
    if kind == "capacity" and cat == "水・飲料":
        continue

    if cat == "トイレ・衛生":
        if unit in ["回", "枚", "袋", ""]:
            amounts[cat] += qty
    else:
        amounts[cat] += qty

    d = iso_to_date(s.get("due_date"))
    if d and d < today:
        expired_count += 1

# =========================================================
# Gemini helpers
# =========================================================
def _clean_json_text(text: str) -> str:
    t = (text or "").strip()
    # code fence除去
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()

def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    t = _clean_json_text(text)
    if not t:
        return []
    # JSON配列部分だけを拾う
    start = t.find("[")
    end = t.rfind("]")
    blob = t
    if start != -1 and end != -1 and end > start:
        blob = t[start : end + 1]

    # まずJSON
    try:
        obj = json.loads(blob)
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return obj
        return []
    except Exception:
        # 次に Python literal（シングルクォート等）救済
        try:
            obj = ast.literal_eval(blob)
            if isinstance(obj, dict):
                return [obj]
            if isinstance(obj, list):
                return obj
        except Exception:
            return []
    return []

def _normalize_date_str(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    d = iso_to_date(s)
    return d.isoformat() if d else ""

def _normalize_ai_item(it: Dict[str, Any], category: str) -> Dict[str, Any]:
    name = str(it.get("name") or it.get("item") or "").strip()
    if not name:
        name = "（品名未設定）"

    qty = it.get("qty", 1)
    try:
        qty = float(qty)
    except Exception:
        qty = 1.0
    if qty <= 0:
        qty = 1.0

    unit = str(it.get("unit") or "").strip()
    subtype = str(it.get("subtype") or "").strip()
    memo = str(it.get("memo") or "").strip()

    due_type = str(it.get("due_type") or "none").strip().lower()
    if due_type not in ("expiry", "inspection", "none"):
        due_type = "none"

    due_date = _normalize_date_str(str(it.get("due_date") or ""))

    # トイレ以外は subtype を空に
    if category != "トイレ・衛生":
        subtype = ""

    if category == "トイレ・衛生" and subtype and subtype not in TOILET_SUBTYPES:
        subtype = "その他"

    # due_type が none なら due_date は空に寄せる
    if due_type == "none":
        due_date = ""

    return {
        "name": name,
        "qty": qty,
        "unit": unit,
        "subtype": subtype,
        "due_type": due_type,
        "due_date": due_date,
        "memo": memo,
    }

def _preprocess_image(uploaded_file, max_side: int = 1280, quality: int = 85) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """iPhone写真が重すぎて遅い/タイムアウトの原因になるので縮小して送る"""
    raw = uploaded_file.getvalue()
    orig_kb = int(len(raw) / 1024)

    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    w, h = img.size

    scale = min(1.0, float(max_side) / float(max(w, h)))
    if scale < 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
    else:
        nw, nh = w, h

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    new_kb = int(len(data) / 1024)

    part = {"mime_type": "image/jpeg", "data": data}
    info = {"orig_kb": orig_kb, "new_kb": new_kb, "orig_px": f"{w}x{h}", "new_px": f"{nw}x{nh}"}
    return part, info

def gemini_extract(uploaded_file, cat: str, model_name: str, timeout_s: int) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    """Gemini呼び出し：ハング回避(REST + timeout) + JSON固定"""
    if genai is None:
        return [], "google-generativeai がインストールされていません。", {}
    if not EFFECTIVE_GEMINI_KEY or not EFFECTIVE_GEMINI_KEY.startswith("AIza"):
        return [], "APIキーが未設定です。環境変数 GEMINI_API_KEY またはサイドバーで設定してください。", {}

    # 画像を軽量化
    image_part, info = _preprocess_image(uploaded_file)

    prompt = f"""
あなたは「防災備蓄品の登録AI」です。
カテゴリ: {cat}

画像から読み取れる備蓄品を抽出し、**JSON配列のみ**を返してください。
前後に説明文、コードブロック ``` は一切禁止。

返すJSONのスキーマ（必須キー）:
[
  {{
    "name": "品名",
    "qty": 1,
    "unit": "単位(L/本/食/回/箱/基など)",
    "subtype": "携帯トイレ|組立トイレ|仮設トイレ|トイレ袋|凝固剤|その他 (トイレカテゴリ以外は空文字)",
    "due_type": "expiry|inspection|none",
    "due_date": "YYYY-MM-DD (不明または期限なしは空文字)",
    "memo": "補足(任意)"
  }}
]

ルール:
- qty は必ず数値。分からなければ 1。
- due_date は西暦(YYYY-MM-DD)。読めなければ空文字。
- due_type が none の場合 due_date は空文字にする。
"""

    # generation_config：JSON固定（使えないSDK版でも落ちないようフォールバック）
    try:
        gconf = genai.GenerationConfig(
            temperature=0.2,
            max_output_tokens=1024,
            response_mime_type="application/json",
        )
    except Exception:
        gconf = genai.GenerationConfig(
            temperature=0.2,
            max_output_tokens=1024,
        )

    try:
        model = genai.GenerativeModel(model_name=model_name)

        # ✅ ここが「無限グルグル」回避の本丸：timeout付ける
        # request_optionsの使用例は公式フォーラムでも言及あり
        result = model.generate_content(
            [prompt, image_part],
            generation_config=gconf,
            request_options={"timeout": int(timeout_s)},
        )
        raw = getattr(result, "text", "") or ""
        items = _extract_json_array(raw)

        norm: List[Dict[str, Any]] = []
        for x in items:
            if isinstance(x, dict):
                norm.append(_normalize_ai_item(x, cat))

        return norm, raw, info

    except Exception as e:
        return [], f"{type(e).__name__}: {e}", info

# =========================================================
# Pages
# =========================================================
def back_home(sfx: str):
    if button_stretch("🔙 ホームに戻る", key=f"back_{sfx}", type="secondary"):
        st.session_state.inv_cat = None
        st.session_state.pending_items = []
        navigate_to("home")

# -----------------------
# Home
# -----------------------
if st.session_state.current_page == "home":
    st.markdown(f"## ⛑️ {APP_TITLE}")
    st.markdown("<p style='text-align:center; color:#64748b; margin-top:-6px;'>物資DX台帳 × 自主点検</p>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if button_stretch("📊\n分析レポート\n(充足率)", key="tile_dash", type="primary"):
            navigate_to("dashboard")
        if button_stretch("✅\n自動自主点検\n(裏取り)", key="tile_insp", type="primary"):
            navigate_to("inspection")
    with c2:
        if button_stretch("📦\n備蓄・登録\n(現場)", key="tile_inv", type="primary"):
            navigate_to("inventory")
        if button_stretch("💾\nデータ管理\n(CSV)", key="tile_data", type="primary"):
            navigate_to("data")

    st.markdown("---")
    if expired_count > 0:
        st.error(f"🚨 期限切れが {expired_count} 件あります")
    else:
        st.success("✅ 期限切れはありません")

# -----------------------
# Inspection
# -----------------------
elif st.session_state.current_page == "inspection":
    back_home("insp")
    st.markdown("## ✅ 自動点検")

    with st.expander("🏢 施設情報", expanded=True):
        f_toilets = st.number_input("既設トイレ(便器数)", 0, 5000, 0, key="f_toilets")

    # 6-5（簡易版：携帯トイレ回数 + 基数）
    p_uses = amounts["トイレ・衛生"]
    units = float(f_toilets) + sum(
        float(s.get("qty", 0) or 0)
        for s in stocks
        if get_cat_key(s.get("category")) == "トイレ・衛生"
        and str(s.get("subtype") or "") in ["仮設トイレ", "組立トイレ"]
    )
    need_uses = max(t_pop * 5 * 3, t_pop * 5 * t_days)  # 最低3日分
    need_units = (t_pop + 49) // 50 if t_days <= 2 else (t_pop + 19) // 20

    ok_65 = (p_uses >= need_uses) and (units >= need_units)

    st.markdown(
        f"""
<div class="card {'card-ok' if ok_65 else 'card-ng'}">
  <b>6-5 簡易トイレ等の備え</b><br>
  判定: {'🟢 適合' if ok_65 else '🔴 不適合'}<br>
  <small>
    携帯トイレ等(回): {int(p_uses):,} / 必要 {int(need_uses):,}<br>
    トイレ基数(基): {int(units):,} / 必要 {int(need_units):,}
  </small>
</div>
""",
        unsafe_allow_html=True,
    )

    ok_71 = amounts["水・飲料"] >= TARGETS["水・飲料"]
    st.markdown(
        f"""
<div class="card {'card-ok' if ok_71 else 'card-ng'}">
  <b>7-1 水・食料の備え（飲料水）</b><br>
  判定: {'🟢 適合' if ok_71 else '🔴 不適合'}<br>
  <small>
    水: {int(amounts["水・飲料"]):,} / 目標 {int(TARGETS["水・飲料"]):,}
  </small>
</div>
""",
        unsafe_allow_html=True,
    )

# -----------------------
# Dashboard
# -----------------------
elif st.session_state.current_page == "dashboard":
    back_home("dash")
    st.markdown("## 📊 充足率")

    for k in ["水・飲料", "主食類", "トイレ・衛生"]:
        target = float(TARGETS.get(k) or 0) or 1.0
        pct = min(float(amounts.get(k) or 0) / target, 1.0)
        st.write(f"**{k}**")
        st.progress(pct)
        st.caption(f"現在: {int(amounts[k]):,} / 目標: {int(TARGETS[k]):,}（{int(pct*100)}%）")

# -----------------------
# Inventory
# -----------------------
elif st.session_state.current_page == "inventory":
    back_home("inv")

    # カテゴリ選択
    if st.session_state.inv_cat is None:
        st.markdown("## 📦 カテゴリ選択")
        cols = st.columns(2)
        for i, (cat, icon) in enumerate(CATEGORIES.items()):
            with cols[i % 2]:
                if button_stretch(
                    f"{icon}\n{cat}\n{int(amounts[cat]):,}",
                    key=f"tile_cat_{cat}",
                    type="primary",
                ):
                    st.session_state.inv_cat = cat
                    st.session_state.pending_items = []
                    st.rerun()

    # カテゴリ詳細
    else:
        cat = st.session_state.inv_cat
        st.markdown(f"## {CATEGORIES[cat]} {cat}")

        if button_stretch("🔙 カテゴリ一覧に戻る", key="back_cat_list", type="secondary"):
            st.session_state.inv_cat = None
            st.session_state.pending_items = []
            st.rerun()

        tab_ai, tab_cart, tab_list = st.tabs(["📸 AI登録", "🛒 カート(未登録)", "📝 登録済みリスト"])

        # ---------- AI登録 ----------
        with tab_ai:
            st.caption(f"モデル: **{selected_model}** / タイムアウト: **{timeout_sec}s** / transport: **REST**")

            # 接続テスト（軽いテキスト生成）
            if st.button("🧪 AI接続テスト（10秒）", type="secondary", use_container_width=True):
                if genai is None:
                    st.error("google-generativeai がありません。requirements を確認してください。")
                elif not (EFFECTIVE_GEMINI_KEY and EFFECTIVE_GEMINI_KEY.startswith("AIza")):
                    st.error("APIキーが未設定です。")
                else:
                    try:
                        m = genai.GenerativeModel(model_name=selected_model)
                        r = m.generate_content(
                            "Say OK",
                            request_options={"timeout": 10},
                        )
                        st.success(f"OK: {getattr(r, 'text', '').strip()[:40]}")
                    except Exception as e:
                        st.error(f"接続テスト失敗: {type(e).__name__}: {e}")

            img_file = st.camera_input("撮影（iPhone対応）")
            if not img_file:
                img_file = st.file_uploader("または画像アップロード", type=["jpg", "jpeg", "png"])

            if img_file is not None:
                st.image(img_file, caption="入力画像（プレビュー）", use_container_width=True)

            if img_file is not None and st.button("解析開始（AI）", type="primary", use_container_width=True):
                with st.spinner("AI解析中...（終わらない場合はタイムアウトで止まります）"):
                    items, raw, info = gemini_extract(img_file, cat, selected_model, timeout_sec)
                    st.session_state.ai_last_raw = raw

                if not items:
                    st.error("AI解析に失敗しました（タイムアウト/モデル名/ネットワーク等）")
                    st.caption(f"詳細: {raw}")
                    with st.expander("デバッグ（AI生出力）"):
                        st.code(st.session_state.ai_last_raw or "", language="text")
                    st.info("対策：①モデルをFlash-Liteにする ②画像が重い場合は撮り直し ③ネットワーク確認 ④REST transportは適用済み")
                else:
                    # カートへ追加（UUIDで識別）
                    for it in items:
                        it2 = dict(it)
                        it2["category"] = cat
                        it2["item_kind"] = "stock"
                        it2["_tmp_id"] = str(uuid.uuid4())
                        st.session_state.pending_items.append(it2)

                    st.success(f"AI抽出: {len(items)}件 → カートに追加しました")
                    st.caption(f"画像軽量化: {info.get('orig_px')} {info.get('orig_kb')}KB → {info.get('new_px')} {info.get('new_kb')}KB")
                    st.rerun()

        # ---------- カート（未登録） ----------
        with tab_cart:
            pending: List[Dict[str, Any]] = st.session_state.pending_items or []
            if not pending:
                st.info("カートは空です（AI登録タブで解析するとここに入ります）")
            else:
                st.warning(f"未登録: {len(pending)}件（ここで修正してからDB登録できます）")

                # まとめて操作
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("🧹 カート全消去", type="secondary", use_container_width=True):
                        st.session_state.pending_items = []
                        st.rerun()
                with col_b:
                    if st.button("✅ この内容でDB登録", type="primary", use_container_width=True):
                        try:
                            payload = []
                            for it in st.session_state.pending_items:
                                payload.append({
                                    "name": it.get("name"),
                                    "qty": it.get("qty"),
                                    "unit": it.get("unit", ""),
                                    "category": it.get("category", cat),
                                    "item_kind": it.get("item_kind", "stock"),
                                    "subtype": it.get("subtype", ""),
                                    "due_type": it.get("due_type", "none"),
                                    "due_date": it.get("due_date", ""),
                                    "memo": it.get("memo", ""),
                                })
                            db.bulk_upsert(payload)
                            st.session_state.pending_items = []
                            st.success("DB登録しました！")
                            st.rerun()
                        except Exception as e:
                            st.error(f"DB登録エラー: {type(e).__name__}: {e}")

                st.markdown("---")

                # 個別編集
                for idx, it in enumerate(list(st.session_state.pending_items)):
                    tmp_id = it.get("_tmp_id", str(idx))
                    title = f"{it.get('name','(no name)')}  ×{it.get('qty',1)}"
                    with st.expander(title, expanded=False):
                        # 削除
                        if st.button("🗑️ この行を削除", key=f"del_pending_{tmp_id}", type="secondary", use_container_width=True):
                            st.session_state.pending_items = [x for x in st.session_state.pending_items if x.get("_tmp_id") != tmp_id]
                            st.rerun()

                        it["name"] = st.text_input("品名", value=str(it.get("name","")), key=f"name_{tmp_id}")
                        it["qty"] = st.number_input("数量", value=float(it.get("qty", 1) or 1), min_value=0.0, step=1.0, key=f"qty_{tmp_id}")
                        it["unit"] = st.text_input("単位", value=str(it.get("unit","")), key=f"unit_{tmp_id}")

                        # トイレ subtype
                        if cat == "トイレ・衛生":
                            cur = str(it.get("subtype","") or "")
                            if cur not in TOILET_SUBTYPES:
                                cur = "その他"
                            it["subtype"] = st.selectbox("種別", TOILET_SUBTYPES, index=TOILET_SUBTYPES.index(cur), key=f"subtype_{tmp_id}")
                        else:
                            it["subtype"] = ""

                        # due_type / due_date
                        due_type_cur = str(it.get("due_type","none") or "none").lower()
                        if due_type_cur not in ["expiry", "inspection", "none"]:
                            due_type_cur = "none"
                        due_type_label_list = ["none", "expiry", "inspection"]
                        due_type_label_map = {"none": "期限なし", "expiry": "賞味期限", "inspection": "点検日"}
                        it["due_type"] = st.selectbox(
                            "期限種別",
                            due_type_label_list,
                            index=due_type_label_list.index(due_type_cur),
                            format_func=lambda x: due_type_label_map.get(x, x),
                            key=f"due_type_{tmp_id}",
                        )

                        if it["due_type"] == "none":
                            it["due_date"] = ""
                            st.caption("期限なし（due_date は空になります）")
                        else:
                            # 初期値
                            date_key = f"due_date_{tmp_id}"
                            if date_key not in st.session_state:
                                d0 = iso_to_date(it.get("due_date")) or today
                                st.session_state[date_key] = d0

                            # クイックボタン（+1/+3/+5年）
                            qc1, qc2, qc3 = st.columns(3)
                            base = today
                            with qc1:
                                if st.button("+1年", key=f"q1_{tmp_id}", use_container_width=True):
                                    nd = date(base.year + 1, base.month, min(base.day, 28) if base.month == 2 else base.day)
                                    st.session_state[date_key] = nd
                                    it["due_date"] = nd.isoformat()
                                    st.rerun()
                            with qc2:
                                if st.button("+3年", key=f"q3_{tmp_id}", use_container_width=True):
                                    nd = date(base.year + 3, base.month, min(base.day, 28) if base.month == 2 else base.day)
                                    st.session_state[date_key] = nd
                                    it["due_date"] = nd.isoformat()
                                    st.rerun()
                            with qc3:
                                if st.button("+5年", key=f"q5_{tmp_id}", use_container_width=True):
                                    nd = date(base.year + 5, base.month, min(base.day, 28) if base.month == 2 else base.day)
                                    st.session_state[date_key] = nd
                                    it["due_date"] = nd.isoformat()
                                    st.rerun()

                            dval = st.date_input("期限日", key=date_key)
                            it["due_date"] = dval.isoformat()

                        it["memo"] = st.text_area("メモ", value=str(it.get("memo","")), key=f"memo_{tmp_id}")

        # ---------- 登録済み ----------
        with tab_list:
            rows = [s for s in stocks if get_cat_key(s.get("category")) == cat]
            if not rows:
                st.info("このカテゴリの登録済みデータはありません")
            else:
                st.caption(f"登録済み: {len(rows)}件")
                for s in rows:
                    name = s.get("name","")
                    qty = s.get("qty",0)
                    due = s.get("due_date","")
                    label = f"{name} (×{int(qty) if float(qty).is_integer() else qty})"
                    if due:
                        label += f" / {due}"

                    with st.expander(label):
                        st.write(f"単位: {s.get('unit','')}")
                        st.write(f"期限種別: {DUE_LABEL.get(str(s.get('due_type','none')), str(s.get('due_type','none')))}")
                        st.write(f"期限日: {s.get('due_date','')}")
                        st.write(f"種別: {s.get('subtype','')}")
                        st.write(f"メモ: {s.get('memo','')}")

                        if st.button("削除", key=f"del_{s.get('id')}", type="secondary", use_container_width=True):
                            db.delete_stock(s.get("id"))
                            st.rerun()

# -----------------------
# Data
# -----------------------
elif st.session_state.current_page == "data":
    back_home("data")
    st.markdown("## 💾 データ管理")

    st.download_button(
        "📥 CSV保存",
        pd.DataFrame(stocks).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"bousai_backup_{datetime.now().strftime('%Y%m%d')}.csv",
        use_container_width=True,
    )

    st.markdown("---")
