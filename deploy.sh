#!/bin/bash
# 蔵書を作り直して GitHub Pages に反映する。
#
#   ./deploy.sh
#
# 合い言葉は聞かれたら入力する（環境変数 KINDLE_ORACLE_PASSPHRASE でも可）。
# 公開されるのは docs/ の中身だけで、ハイライト本文は暗号化された
# index.enc の中にしか存在しない。

set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python

if [ ! -x "$PY" ]; then
  echo "先に venv を作ること: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "── 1/4 Obsidian からハイライトを読む"
"$PY" parse_highlights.py

echo
echo "── 2/4 埋め込みを作る"
if [ ! -f model/onnx/model_quantized.onnx ]; then
  echo "モデルが無いので取得する"
  "$PY" fetch_model.py
fi
"$PY" build_index.py

echo
echo "── 3/4 合い言葉で暗号化する"
"$PY" pack.py

echo
echo "── 4/4 GitHub へ反映する"
if [ -z "$(git status --porcelain docs)" ]; then
  echo "docs に変更なし。push は省略。"
  exit 0
fi
git add docs
git commit -m "蔵書を更新 ($(date '+%Y-%m-%d'))"
git push

echo
echo "完了。iPhone 側は ⚙ →「蔵書データを削除して入れ直す」で新しい版になる。"
