# VPS の app.py に EFFECTIVE_GEMINI_KEY を入れる（手動）

VPS 上の `/opt/bousai_dx/app.py` に `EFFECTIVE_GEMINI_KEY` が無い場合（環境変数があっても「毎回入力」になる場合）の手順。

## 1) 壊れた verify を削除

```bash
sudo rm -f /tmp/bousai_verify.sh
```

## 2) 行番号を確認

```bash
cd /opt/bousai_dx
grep -n "ENV_GEMINI" app.py | head
grep -n "genai.configure" app.py | head
```

## 3) 編集

```bash
nano app.py
```

- **追加**（サイドバーが終わる直後、`genai.configure` の前が分かりやすい）  
  - 1行追加:
    ```python
    EFFECTIVE_GEMINI_KEY = (st.session_state.get("api_key") or ENV_GEMINI or "").strip()
    ```
- **置換**（`genai.configure` の行）  
  - 次のようにする:
    ```python
    genai.configure(api_key=EFFECTIVE_GEMINI_KEY, transport="rest")
    ```
  - もし `genai.configure(api_key=api_key, ...)` になっていたら、`api_key=api_key` を `api_key=EFFECTIVE_GEMINI_KEY` に変更。

保存: `Ctrl+O` → Enter → `Ctrl+X`

## 4) 再起動

```bash
sudo systemctl restart bousai_dx
```

## 5) 確認（コピペで実行）

`scripts/vps_verify_inline.sh` の中身を VPS でそのままコピペして実行するか、  
リポジトリから `scp scripts/vps_verify_inline.sh user@vps:/tmp/` で送って `bash /tmp/vps_verify_inline.sh` を実行。

**4つ揃えば完了:**
- `=== 2) ===` で `OK: no old configure(api_key=api_key)` と `OK: no hardcoded GEMINI_API_KEY`
- `=== 3) ===` で `GEMINI_API_KEY=SET`
- `=== 4) ===` で `OK: 127.0.0.1:8501 is up (HTTP 200)` と `ss` で 8501 LISTEN
