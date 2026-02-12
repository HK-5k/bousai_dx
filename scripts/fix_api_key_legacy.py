#!/usr/bin/env python3
"""
app.py 内の「古い api_key 運用」3箇所を置換する。
- st.session_state.api_key = api_key → 空上書きしないブロックに
- if not st.session_state.api_key: → EFFECTIVE 判定に（2箇所）

実行: python3 scripts/fix_api_key_legacy.py
（app.py はカレントディレクトリまたは --path で指定）
"""
import argparse
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="app.py", help="app.py のパス")
    ap.add_argument("--dry-run", action="store_true", help="書き換えせず表示のみ")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"not found: {p}")

    lines = p.read_text(encoding="utf-8").splitlines(True)
    out = []
    n_assign = 0
    n_if = 0

    for line in lines:
        s = line.strip()

        # st.session_state.api_key = api_key を「空入力で上書きしない」形に
        if s in ("st.session_state.api_key = api_key", "st.session_state.api_key=api_key"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + 'api_key = (api_key or "").strip()\n')
            out.append(indent + "# 空入力では上書きしない（サーバーENVを優先）\n")
            out.append(indent + "if api_key:\n")
            out.append(indent + '    st.session_state["api_key"] = api_key\n')
            out.append(indent + "elif ENV_GEMINI:\n")
            out.append(indent + '    st.session_state["api_key"] = ENV_GEMINI\n')
            n_assign += 1
            continue

        # if not st.session_state.api_key: を ENV も含めた判定に
        if s == "if not st.session_state.api_key:":
            indent = line[: len(line) - len(line.lstrip())]
            out.append(
                indent
                + 'if not ((st.session_state.get("api_key") or ENV_GEMINI or "").strip().startswith("AIza")):\n'
            )
            n_if += 1
            continue

        out.append(line)

    if args.dry_run:
        print("".join(out))
        print(f"[dry-run] would patch: assignment={n_assign}, if={n_if}", file=__import__("sys").stderr)
        return

    p.write_text("".join(out), encoding="utf-8")
    print(f"patched: assignment={n_assign}, if={n_if}")


if __name__ == "__main__":
    main()
