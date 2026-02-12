#!/bin/bash
APP=/opt/bousai_dx/app.py

echo "=== 1) key markers ==="
grep -nE "ENV_GEMINI|EFFECTIVE_GEMINI_KEY|genai\.configure" "$APP" 2>/dev/null | head -n 30

echo ""
echo "=== 2) bad pattern (must be OK) ==="
grep -n "genai.configure(api_key=api_key" "$APP" 2>/dev/null && echo "FAIL: old configure found" || echo "OK: no old configure(api_key=api_key)"

echo ""
echo "=== 3) streamlit up? ==="
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 2>/dev/null || echo "000")
if [ "$code" = "200" ]; then
  echo "OK: 127.0.0.1:8501 HTTP 200"
else
  echo "wait... (code=$code)"
fi
ss -lntp 2>/dev/null | grep ":8501" || echo "NOT LISTENING"
