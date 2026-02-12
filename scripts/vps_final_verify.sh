#!/bin/bash
# VPS で「修正完了」を一発判定。空白の揺れ・定義回数・再起動ループ・hardcoded キーも確認。
# set -e 使用のため、grep で 0 件のとき exit 1 にならないよう該当箇所は ( ) または || true で保護。
# 使い方: scp scripts/vps_final_verify.sh user@vps:/tmp/ && ssh user@vps 'bash /tmp/vps_final_verify.sh'

set -e
cd /opt/bousai_dx
APP=/opt/bousai_dx/app.py
PY=/opt/bousai_dx/.venv/bin/python3
SVC=bousai_dx

echo "=== 1) syntax gate ==="
$PY -m py_compile "$APP" && echo "OK: py_compile" || { echo "NG: py_compile"; exit 1; }

echo
echo "=== 2) marker lines (for humans) ==="
grep -nE '^[[:space:]]*ENV_GEMINI[[:space:]]*=|^[[:space:]]*EFFECTIVE_GEMINI_KEY[[:space:]]*=|genai\.configure' "$APP" | head -n 220 || true

echo
echo "=== 3) definition counts (should be 1 each) ==="
cnt_env=$(grep -cE '^[[:space:]]*ENV_GEMINI[[:space:]]*=' "$APP" 2>/dev/null || true)
cnt_eff=$(grep -cE '^[[:space:]]*EFFECTIVE_GEMINI_KEY[[:space:]]*=' "$APP" 2>/dev/null || true)
echo "ENV_GEMINI defs: $cnt_env"
echo "EFFECTIVE_GEMINI_KEY defs: $cnt_eff"
[ "${cnt_env:-0}" -eq 1 ] && echo "OK: ENV_GEMINI defined once" || echo "NG: ENV_GEMINI not exactly once"
[ "${cnt_eff:-0}" -eq 1 ] && echo "OK: EFFECTIVE_GEMINI_KEY defined once" || echo "NG: EFFECTIVE_GEMINI_KEY not exactly once"

echo
echo "=== 4) must-not old configure(api_key=api_key) (line-start anchor, no comment false positive) ==="
if grep -nE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*api_key' "$APP" 2>/dev/null; then
  echo "NG: old configure remains"
else
  echo "OK: old configure absent"
fi

echo
echo "=== 5) must configure uses EFFECTIVE (line-start anchor) ==="
if grep -nE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*EFFECTIVE_GEMINI_KEY' "$APP" 2>/dev/null; then
  echo "OK: configure uses EFFECTIVE"
else
  echo "NG: configure not using EFFECTIVE"
fi

echo
echo "=== 6) must-not hardcoded GEMINI_API_KEY= in code ==="
(grep -nE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$APP" 2>/dev/null && echo "NG: hardcoded GEMINI_API_KEY") || echo "OK: no hardcoded GEMINI_API_KEY"

echo
echo "=== 7) restart service & check stability ==="
sudo systemctl restart "$SVC"
sudo systemctl show "$SVC" -p NRestarts -p ActiveState -p SubState -p ExecMainStatus --no-pager

echo
echo "=== 8) systemd -> process env (masked) ==="
pid=$(systemctl show -p MainPID --value "$SVC" 2>/dev/null || true)
echo "MainPID=$pid"
if [ -n "${pid:-}" ] && [ -r "/proc/$pid/environ" ]; then
  sudo tr '\0' '\n' </proc/"$pid"/environ | egrep '^(GEMINI_API_KEY|OPENAI_API_KEY)=' | sed 's/=.*/=SET/' || true
else
  echo "WARN: cannot read /proc/$pid/environ"
fi

echo
echo "=== 9) up check (127.0.0.1:8501) ==="
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    echo "OK: HTTP 200"
    break
  fi
  echo "wait... $i/25 (code=$code)"
  sleep 1
done
sudo ss -lntp 2>/dev/null | grep ':8501' || echo "NOT LISTENING"

echo
echo "=== 10) last logs ==="
sudo journalctl -u "$SVC" -n 80 --no-pager
