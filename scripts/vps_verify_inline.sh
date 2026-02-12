#!/bin/bash
# VPS で「今の状態」を確実に判定する。壊れた /tmp/bousai_verify.sh は削除してから実行。
#
# 使い方: 中身を VPS でコピペするか、scp で送って bash /tmp/vps_verify_inline.sh
#
# 判定基準（4つ揃えば完了）:
#   === 2) === OK: no old configure(api_key=api_key) / OK: no hardcoded GEMINI_API_KEY
#   === 3) === GEMINI_API_KEY=SET
#   === 4) === OK: 127.0.0.1:8501 is up (HTTP 200) と ss で 8501 LISTEN

sudo rm -f /tmp/bousai_verify.sh

sudo -v
APP=/opt/bousai_dx/app.py

echo "=== 1) app.py: key markers ==="
grep -nE "ENV_GEMINI|EFFECTIVE_GEMINI_KEY|genai\.configure" "$APP" | sed -n '1,120p' || true

echo
echo "=== 2) app.py: bad patterns (should be empty) ==="
grep -n "genai.configure(api_key=api_key" "$APP" 2>/dev/null || echo "OK: no old configure(api_key=api_key)"
grep -nE "^[[:space:]]*GEMINI_API_KEY[[:space:]]*=" "$APP" 2>/dev/null || echo "OK: no hardcoded GEMINI_API_KEY"

echo
echo "=== 3) systemd -> process env (masked) ==="
pid=$(systemctl show -p MainPID --value bousai_dx 2>/dev/null)
echo "MainPID=$pid"
sudo tr '\0' '\n' </proc/"$pid"/environ 2>/dev/null | egrep "^(GEMINI_API_KEY|OPENAI_API_KEY)=" | sed 's/=.*/=SET/' || true

echo
echo "=== 4) restart & wait for 127.0.0.1:8501 ==="
sudo systemctl restart bousai_dx
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    echo "OK: 127.0.0.1:8501 is up (HTTP 200)"
    break
  fi
  echo "wait... $i/25 (code=$code)"
  sleep 1
done
sudo ss -lntp | grep ":8501" || echo "NOT LISTENING"
sudo journalctl -u bousai_dx -n 80 --no-pager
