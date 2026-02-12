#!/bin/bash
# VPS で bousai_dx 再起動後、Streamlit が本当に起動したか確認する。
# 起動に数秒かかることがあるので、最大15秒待ってから疎通・リスニング・ログを表示する。
#
# 使い方: sudo systemctl restart bousai_dx && bash scripts/check_streamlit_up.sh

set -e
PORT=8501
MAX=15

echo "=== waiting for Streamlit (up to ${MAX}s) ==="
for i in $(seq 1 $MAX); do
  if curl -s -I "http://127.0.0.1:${PORT}" 2>/dev/null | head -n 1 | grep -q "200"; then
    echo "OK: streamlit is up"
    break
  fi
  echo "waiting... ($i/$MAX)"
  sleep 1
  if [ "$i" -eq "$MAX" ]; then
    echo "TIMEOUT: no 200 after ${MAX}s"
  fi
done

echo "=== listen check ==="
sudo ss -lntp 2>/dev/null | grep ":$PORT" || echo "NOT LISTENING on $PORT"

echo "=== service state ==="
sudo systemctl show bousai_dx -p NRestarts -p ActiveState -p SubState -p ExecMainStatus -p ExecMainCode --no-pager 2>/dev/null || true

echo "=== last logs ==="
sudo journalctl -u bousai_dx -n 80 --no-pager 2>/dev/null || true
