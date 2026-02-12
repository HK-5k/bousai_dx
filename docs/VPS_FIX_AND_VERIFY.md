# VPS: app.py を確実に直す → 完了判定

**重要:** コピペ混入で app.py が壊れている場合は、パッチ当てより **app.py をクリーンに置き換える**のが最短です。  
**py_compile が通るまで `systemctl restart` しない**（ここが事故防止の最大ポイント）です。

---

## 0) 念のため：転送前ゲート（送る前に必ず通す）

すでに通している場合も、転送直前に再実行するだけで OK です。

```bash
cd /Users/kagetu/bousai_dx

python3 -m py_compile app.py && echo "OK: py_compile"
bash scripts/local_gate.sh

bash -n scripts/vps_final_verify.sh && echo "OK: bash -n"
head -n 5 scripts/vps_final_verify.sh
```

**見るポイント**

- **head -n 5** の 1 行目が `#!/bin/bash`（または `#!/usr/bin/env bash`）で、その前にゴミ行が無いこと。
- **bash -n** が通ること（スクリプトが「1 行潰れ」していないこと）。

ここまで OK なら、次に進みます。

---

## 1) app.py 側で「旧 configure が絶対に残らない」保証（ローカルで送る前のゲート）

VPS に送る前に、**リポジトリルートで必ず通す**ことで、壊れたファイルを直すより「repo の正しい app.py を scp して上書き」が一番事故りません。

**条件（repo の app.py が満たすこと）:**

- `genai.configure(api_key=api_key` が **0 件**
- `genai.configure(api_key=EFFECTIVE_GEMINI_KEY` が **1 件以上**（try/except で 2 行になるのは OK）
- `ENV_GEMINI = ...` と `EFFECTIVE_GEMINI_KEY = ...` が **それぞれ 1 回だけ**

**ローカル（Mac）で実行:**

```bash
# リポジトリルートで
python3 -m py_compile app.py && echo "OK: py_compile"
grep -nE 'genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*api_key' app.py && echo "NG: old configure" || echo "OK: no old configure"
grep -cE '^[[:space:]]*ENV_GEMINI[[:space:]]*=' app.py
grep -cE '^[[:space:]]*EFFECTIVE_GEMINI_KEY[[:space:]]*=' app.py
```

- `ENV_GEMINI` / `EFFECTIVE_GEMINI_KEY` のカウントは **1** であること。
- まとめて実行する場合は `bash scripts/local_gate.sh`（上記と同じチェック＋**空上書きの残骸** `st.session_state.api_key =` の有無＋行頭アンカー付き grep でコメント誤検知を防ぐ）。

---

## 2) vps_final_verify.sh の「壊れにくさ」確認（VPS に送る前）

スクリプトが改行なしで 1 行に潰れていないか、先頭が shebang かどうかをローカルで確認します。

```bash
bash -n scripts/vps_final_verify.sh && echo "OK: bash -n"
head -n 5 scripts/vps_final_verify.sh
```

- 先頭が **1 行目から `#!/bin/bash`**（または `#!/usr/bin/env bash`）で、その前に余計な `echo` などが無いこと。

---

## 3) VPS に反映する「最短・事故らない」実運用手順

**ローカル（Mac）→ VPS へ転送**

```bash
scp app.py scripts/vps_final_verify.sh machikagami-vps:/tmp/
```

**VPS 側（この順番固定）**

```bash
sudo systemctl stop bousai_dx

# 止まったことを確認（8501 が LISTEN してたら止まり切ってない）
sudo ss -lntp | grep ':8501' || echo "OK: 8501 not listening"

cd /opt/bousai_dx
cp -a app.py app.py.bak_$(date +%Y%m%d_%H%M%S)

# クリーン上書き（最重要）
sudo cp -a /tmp/app.py /opt/bousai_dx/app.py

# 再起動前ゲート（ここで落ちたら絶対に restart しない）
/opt/bousai_dx/.venv/bin/python3 -m py_compile /opt/bousai_dx/app.py && echo "OK: py_compile" || exit 1

# verify 実行
chmod +x /tmp/vps_final_verify.sh
bash /tmp/vps_final_verify.sh
```

**以下が 全部揃えば「修正完了」**

- OK: py_compile
- ENV_GEMINI 定義回数 = 1、EFFECTIVE_GEMINI_KEY 定義回数 = 1
- `genai.configure(... api_key=api_key ...)` が 0 件
- `genai.configure(... api_key=EFFECTIVE_GEMINI_KEY ...)` が 1 件以上
- GEMINI_API_KEY=SET（`/proc/$pid/environ` で確認。値はマスク）
- HTTP 200（http://127.0.0.1:8501）& `ss` で `:8501` LISTEN
- `systemctl show` で ActiveState=active / SubState=running / ExecMainStatus=0 かつ NRestarts が増え続けない

※ HTTP 200 / LISTEN だけでは「ENV のキーが使われた証拠」にならない（起動してるだけの可能性がある）ので、上のセット判定を採用。

この流れなら、heredoc 混入・SyntaxError の再発をほぼ潰せます。

---

## 4) 推奨: VPS を確実に直す最短手順（クリーンに戻す選択肢）

### 1) サービス停止（壊れた状態で再起動しない）

```bash
sudo systemctl stop bousai_dx
```

### 2) app.py をクリーンに戻す（どれか 1 つ）

**A. VPS が git 管理なら（最強に安全）**

```bash
cd /opt/bousai_dx
git status --porcelain
git restore app.py
git pull --ff-only
```

**B. バックアップから戻す（VPS 単体で完結）**

```bash
cd /opt/bousai_dx
ls -1t app.py.bak_* | head
# 直近の「壊す前」を選んで戻す
cp -a app.py.bak_api_fix_YYYYMMDD_HHMMSS app.py
```

**C. ローカルリポジトリの app.py を scp で上書き（git が無くても OK）**

ローカル（Mac）側から:

```bash
scp app.py machikagami-vps:/tmp/app.py.new
ssh machikagami-vps 'cp -a /tmp/app.py.new /opt/bousai_dx/app.py'
```

### 3) 再起動前のゲート（ここで落ちたら再起動しない）

```bash
/opt/bousai_dx/.venv/bin/python3 -m py_compile /opt/bousai_dx/app.py && echo "py_compile OK"
```

**「py_compile OK」が出るまで `systemctl restart` しない。**  
SyntaxError のまま再起動すると起動直後に落ちます。

### 4) 修正完了を一発判定する確認コマンド（VPS でそのまま貼り付けて実行）

空白の揺れ・定義回数・再起動ループも見ます。**行頭アンカー `^[[:space:]]*` 付き**で、コメントに旧コードを例示していても誤検知しません。キーはマスク表示されます。

```bash
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
cnt_env=$(grep -cE '^[[:space:]]*ENV_GEMINI[[:space:]]*=' "$APP" || true)
cnt_eff=$(grep -cE '^[[:space:]]*EFFECTIVE_GEMINI_KEY[[:space:]]*=' "$APP" || true)
echo "ENV_GEMINI defs: $cnt_env"
echo "EFFECTIVE_GEMINI_KEY defs: $cnt_eff"
[ "$cnt_env" -eq 1 ] && echo "OK: ENV_GEMINI defined once" || echo "NG: ENV_GEMINI not exactly once"
[ "$cnt_eff" -eq 1 ] && echo "OK: EFFECTIVE_GEMINI_KEY defined once" || echo "NG: EFFECTIVE_GEMINI_KEY not exactly once"

echo
echo "=== 4) must-not old configure(api_key=api_key) (行頭アンカーでコメント誤検知防止) ==="
if grep -nE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*api_key' "$APP"; then
  echo "NG: old configure remains"
else
  echo "OK: old configure absent"
fi

echo
echo "=== 5) must configure uses EFFECTIVE (行頭アンカー) ==="
if grep -nE '^[[:space:]]*genai\.configure\([^)]*api_key[[:space:]]*=[[:space:]]*EFFECTIVE_GEMINI_KEY' "$APP"; then
  echo "OK: configure uses EFFECTIVE"
else
  echo "NG: configure not using EFFECTIVE"
fi

echo
echo "=== 6) must-not hardcoded GEMINI_API_KEY= in code ==="
grep -nE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$APP" 2>/dev/null && echo "NG: hardcoded GEMINI_API_KEY" || echo "OK: no hardcoded GEMINI_API_KEY"

echo
echo "=== 7) restart service & check stability ==="
sudo systemctl restart "$SVC"
sudo systemctl show "$SVC" -p NRestarts -p ActiveState -p SubState -p ExecMainStatus --no-pager

echo
echo "=== 8) systemd -> process env (masked) ==="
pid=$(systemctl show -p MainPID --value "$SVC" 2>/dev/null || true)
echo "MainPID=$pid"
if [ -n "${pid:-}" ] && [ -r "/proc/$pid/environ" ]; then
  sudo tr '\0' '\n' </proc/"$pid"/environ | egrep '^(GEMINI_API_KEY|OPENAI_API_KEY)=' | sed 's/=.*/=SET/'
else
  echo "WARN: cannot read /proc/$pid/environ"
fi

echo
echo "=== 9) up check (127.0.0.1:8501) ==="
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 || true)
  if [ "$code" = "200" ]; then
    echo "OK: HTTP 200"
    break
  fi
  echo "wait... $i/25 (code=$code)"
  sleep 1
done
sudo ss -lntp | grep ':8501' || echo "NOT LISTENING"

echo
echo "=== 10) last logs ==="
sudo journalctl -u "$SVC" -n 80 --no-pager
```

### 合格ライン（この 6 つ＋安定性が揃えば「完了」）

| 項目 | 期待する出力 |
|------|----------------|
| 1) syntax gate | `OK: py_compile` |
| 3) definition counts | `OK: ENV_GEMINI defined once` と `OK: EFFECTIVE_GEMINI_KEY defined once` |
| 4) old configure | `OK: old configure absent` |
| 5) EFFECTIVE | `OK: configure uses EFFECTIVE` |
| 6) hardcoded | `OK: no hardcoded GEMINI_API_KEY` |
| 8) env | `GEMINI_API_KEY=SET`（プロセスに渡っている） |
| 9) up check | `OK: HTTP 200` と `ss` で `:8501` LISTEN |

**7) restart & stability:** `systemctl show` で **ActiveState=active / SubState=running / ExecMainStatus=0** が安定していること。**NRestarts** が増え続ける場合はクラッシュ→再起動ループの可能性があるので要確認。

### 最終的な「修正完了」の証拠

上記「**以下が 全部揃えば「修正完了」**」のチェックリストが揃ってはじめて完了です（※ 同上：HTTP 200 / LISTEN 単体では不十分）。

---

### vps_final_verify が落ちたときの即復旧ルート

**py_compile が NG なら**、その時点で stop のままにして、バックアップに戻すのが最短です。

```bash
cd /opt/bousai_dx
ls -1t app.py.bak_* | head
sudo cp -a app.py.bak_YYYYMMDD_HHMMSS app.py
/opt/bousai_dx/.venv/bin/python3 -m py_compile /opt/bousai_dx/app.py && echo "OK: py_compile"
sudo systemctl restart bousai_dx
```

原因を一点特定したいときは、次のどちらかを貼ってもらえれば「どこを直すか」まで詰められます。

- `bash /tmp/vps_final_verify.sh` の出力（丸ごと）
- もしくは `/opt/bousai_dx/app.py` の **genai.configure 周辺（±30 行）** だけ

---

## いまの VPS の「現実」に対する注意

過去の VPS の grep 結果では **genai.configure(api_key=api_key, ...)** が複数残存しており、その時点では**未完了**でした。さらに **SyntaxError (line 218: '(' was never closed)** も出ていたため、**「クリーンに置き換え」→「py_compile ゲート」→「restart」**の順で戻すのが正解です。

必要なら、現状の `/opt/bousai_dx/app.py` の **genai.configure 周辺（±30 行）** だけを貼ってもらえれば、「3 箇所残っている configure を確実に 1 箇所に統一する」最小修正案を出せます。

---

## 補足: nano でピンポイント修正する場合（パッチ当て）

VPS が git で管理されていて、かつ **ファイルが壊れていない**（py_compile が通る）場合だけ、nano で直す方法もあります。  
**壊れている・SyntaxError が出る場合は上記「クリーンに置き換え」を優先してください。**

1. **ENV_GEMINI を定義**（import の後・`st.set_page_config` より前）  
   - `ENV_GEMINI = (os.getenv("GEMINI_API_KEY") or ...).strip()` と `ENV_OPENAI = ...` を 1 回だけ追加。
2. **EFFECTIVE_GEMINI_KEY を定義**（最初の `genai.configure` の直前に 1 行追加）。
3. **旧 configure をすべて置換**  
   - `genai.configure(api_key=api_key, ...)` → `genai.configure(api_key=EFFECTIVE_GEMINI_KEY, ...)`（3 箇所など）。
4. **空上書きの止血**  
   - `st.session_state.api_key = api_key` → `st.session_state["api_key"] = (api_key or st.session_state.get("api_key") or ENV_GEMINI or "").strip()`。

**保存後、必ず py_compile を通してから restart:**

```bash
/opt/bousai_dx/.venv/bin/python3 -m py_compile /opt/bousai_dx/app.py && echo "py_compile OK"
# OK が出たらのみ
sudo systemctl restart bousai_dx
```

---

## 仕上げの"崩れ検知"コマンド（ローカルでOK）

潰れ事故が残ってないか、これで一発チェックできます。

```bash
# ```bash の直後に文字が続いてたらNG（本来は改行が必要）
PAT1='```'
PAT2='bash[^[:space:]]'
grep -nE "${PAT1}${PAT2}" docs/VPS_FIX_AND_VERIFY.md || echo "OK: code fence is fine"

# 太字の直後へ箇条書きの - が同一行で繋がってたらNG
grep -nE '\*\*.*\*\*-' docs/VPS_FIX_AND_VERIFY.md || echo "OK: list formatting is fine"
```

この2つが OK になれば、Markdown は綺麗に表示されるはずです。
