#!/bin/bash
# VPS で app.py の「古い api_key 3箇所」を一括置換してから再起動する。
# 使い方: cd /opt/bousai_dx && bash scripts/run_fix_api_key_on_vps.sh
# または: scp でこのファイルと scripts/fix_api_key_legacy.py を VPS に送ってから実行

set -e
cd /opt/bousai_dx 2>/dev/null || cd "$(dirname "$0")/.."

echo "=== backup ==="
cp -a app.py "app.py.bak_api_fix_$(date +%Y%m%d_%H%M%S)"

echo "=== patch ==="
python3 scripts/fix_api_key_legacy.py --path app.py

echo "=== syntax check ==="
python3 -m py_compile app.py && echo "py_compile: OK"

echo "=== legacy pattern check (must show OK) ==="
grep -nE 'st\.session_state\.api_key\s*=|if not st\.session_state\.api_key' app.py 2>/dev/null || echo "OK"

echo "=== restart ==="
sudo systemctl restart bousai_dx
sudo journalctl -u bousai_dx -n 30 --no-pager
