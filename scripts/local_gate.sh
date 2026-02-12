#!/bin/bash
# リポジトリの app.py が「VPS に送ってよい」状態かどうか、送る前にローカルで必ず通すゲート。
# 使い方: リポジトリルートで bash scripts/local_gate.sh

set -e
cd "$(dirname "$0")/.."
APP=app.py

echo "=== 1) py_compile ==="
python3 -m py_compile "$APP" && echo "OK: py_compile" || { echo "NG: py_compile"; exit 1; }

echo
echo "=== 2) must-not old configure(api_key=api_key) (line-start anchor) ==="
if grep -nE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*api_key' "$APP" 2>/dev/null; then
  echo "NG: old configure remains"
  exit 1
fi
echo "OK: no old configure"

echo
echo "=== 2b) must-not dot-assign api_key (空上書きの残骸) ==="
if grep -nE 'st\.session_state\.api_key[[:space:]]*=' "$APP" 2>/dev/null; then
  echo "NG: dot-assign api_key remains"
  exit 1
fi
echo "OK: no st.session_state.api_key = ..."

echo
echo "=== 3) ENV_GEMINI defined once ==="
cnt_env=$(grep -cE '^[[:space:]]*ENV_GEMINI[[:space:]]*=' "$APP" 2>/dev/null || true)
echo "ENV_GEMINI defs: $cnt_env"
[ "${cnt_env:-0}" -eq 1 ] || { echo "NG: ENV_GEMINI not exactly once"; exit 1; }
echo "OK: ENV_GEMINI defined once"

echo
echo "=== 4) EFFECTIVE_GEMINI_KEY defined once ==="
cnt_eff=$(grep -cE '^[[:space:]]*EFFECTIVE_GEMINI_KEY[[:space:]]*=' "$APP" 2>/dev/null || true)
echo "EFFECTIVE_GEMINI_KEY defs: $cnt_eff"
[ "${cnt_eff:-0}" -eq 1 ] || { echo "NG: EFFECTIVE_GEMINI_KEY not exactly once"; exit 1; }
echo "OK: EFFECTIVE_GEMINI_KEY defined once"

echo
echo "=== 5) configure uses EFFECTIVE (1件以上, line-start anchor) ==="
cnt_cfg=$(grep -cE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*EFFECTIVE_GEMINI_KEY' "$APP" 2>/dev/null || true)
echo "genai.configure(api_key=EFFECTIVE_GEMINI_KEY) count: $cnt_cfg"
[ "${cnt_cfg:-0}" -ge 1 ] || { echo "NG: no configure with EFFECTIVE_GEMINI_KEY"; exit 1; }
echo "OK: configure uses EFFECTIVE"

echo
echo ">>> local gate: all OK (app.py is safe to scp to VPS)"
