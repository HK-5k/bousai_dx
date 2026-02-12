#!/bin/bash
# コピペ1ブロック用。VPSで: cd /opt/bousai_dx のあと、このファイルを bash で実行するか、
# 中身を丸ごと貼り付けて実行。python3 - <<'PY' から PY まで他のコマンドを挟まないこと。
#
# 使い方例:
#   cd /opt/bousai_dx
#   bash scripts/fix_api_key_oneblock.sh

set -e
cd /opt/bousai_dx 2>/dev/null || true

cp -a app.py "app.py.bak_api_fix_$(date +%Y%m%d_%H%M%S)"

python3 - <<'PY'
from pathlib import Path

p = Path("app.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

out = []
n_assign = 0
n_if = 0

for line in lines:
    s = line.strip()

    if s in ("st.session_state.api_key = api_key", "st.session_state.api_key=api_key"):
        indent = line[:len(line) - len(line.lstrip())]
        out.append(indent + 'api_key = (api_key or "").strip()\n')
        out.append(indent + '# 空入力では上書きしない（サーバーENVを優先）\n')
        out.append(indent + 'if api_key:\n')
        out.append(indent + '    st.session_state["api_key"] = api_key\n')
        out.append(indent + 'elif ENV_GEMINI:\n')
        out.append(indent + '    st.session_state["api_key"] = ENV_GEMINI\n')
        n_assign += 1
        continue

    if s == "if not st.session_state.api_key:":
        indent = line[:len(line) - len(line.lstrip())]
        out.append(indent + 'if not ((st.session_state.get("api_key") or ENV_GEMINI or "").strip().startswith("AIza")):\n')
        n_if += 1
        continue

    out.append(line)

p.write_text("".join(out), encoding="utf-8")
print(f"patched: assignment={n_assign}, if={n_if}")
PY

python3 -m py_compile app.py && echo "py_compile: OK"
grep -nE 'st\.session_state\.api_key\s*=|if not st\.session_state\.api_key' app.py 2>/dev/null || echo "OK"
sudo systemctl restart bousai_dx
sudo journalctl -u bousai_dx -n 30 --no-pager
