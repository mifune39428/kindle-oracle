#!/bin/bash
# Kindle の新しいハイライトとメモを取り込んで、蔵書を作り直して公開するまでを通す。
# launchd から毎朝呼ばれる。手で叩いてもよい。
#
#   ./auto_update.sh          通常（変化した本だけ取り直す）
#   ./auto_update.sh --full   全冊を取り直す（20 分ほどかかる）
#
# Amazon の notebook を直接見る。Obsidian のプラグインは同期を手で
# 起こさないと動かず、実際 6 週間分が抜けていたので使わない。
#
# 最初に一度だけ必要な準備:
#   .venv/bin/python kindle_login.py                       Amazon にログイン
#   security add-generic-password -a "$USER" \
#            -s kindle-oracle-passphrase -w                合い言葉を預ける

set -uo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
STAMP=.last_highlights_hash
LOG_TAG="[$(date '+%Y-%m-%d %H:%M')]"
MODE=${1:-}

log() { echo "$LOG_TAG $*"; }
notify() {   # 人の手が要るときだけ知らせる
  osascript -e "display notification \"$1\" with title \"本に聞く\"" 2>/dev/null || true
}

# ── 1. Amazon から取り込む ───────────────────────────────────────
log "Kindle のハイライトを取りに行く"
# macOS の bash 3.2 は set -u の下で空配列の展開を落とすので、配列は使わない
FETCH_ARGS=""
[ "$MODE" = "--full" ] && FETCH_ARGS="--full"

if ! "$PY" fetch_kindle_web.py $FETCH_ARGS 2>&1 | sed "s/^/$LOG_TAG /"; then
  log "取得に失敗した（上の行に理由が出ている）"
  notify "Kindle の取得に失敗しました。ログを確認してください"
  exit 1
fi

# ── 2. 変化が無ければ何もしない ──────────────────────────────────
new_hash=$(shasum -a 256 highlights.jsonl | cut -d' ' -f1)
if [ "$MODE" != "--full" ] && [ -f "$STAMP" ] && [ "$new_hash" = "$(cat "$STAMP")" ]; then
  log "ハイライトに変化なし。作り直しは省略"
  exit 0
fi

# ── 3. 埋め込み → 暗号化 → 公開 ─────────────────────────────────
PASSPHRASE=$(security find-generic-password -a "$USER" \
              -s kindle-oracle-passphrase -w 2>/dev/null)
if [ -z "$PASSPHRASE" ]; then
  log "キーチェーンに合い言葉が無い"
  notify "合い言葉がキーチェーンにありません。README の手順で登録してください"
  exit 1
fi

log "埋め込みを作り直す"
"$PY" build_index.py | tail -4 | sed "s/^/$LOG_TAG /"

log "暗号化する"
KINDLE_ORACLE_PASSPHRASE="$PASSPHRASE" "$PY" pack.py | sed "s/^/$LOG_TAG /"
unset PASSPHRASE

if [ -z "$(git status --porcelain docs)" ]; then
  log "docs に変化なし。push しない"
  echo "$new_hash" > "$STAMP"
  exit 0
fi

git add docs
git commit -q -m "蔵書を更新 ($(date '+%Y-%m-%d'))"
if git push -q; then
  echo "$new_hash" > "$STAMP"
  books=$("$PY" -c "import json;print(len(json.load(open('meta.json'))['books']))")
  count=$("$PY" -c "import json;print(json.load(open('meta.json'))['count'])")
  log "公開した: ${books} 冊 / ${count} ハイライト"
  notify "蔵書を更新しました（${books} 冊 / ${count} ハイライト）"
else
  log "push に失敗した"
  notify "蔵書の公開に失敗しました。ログを確認してください"
  exit 1
fi
