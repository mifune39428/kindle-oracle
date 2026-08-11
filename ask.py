#!/usr/bin/env python3
"""悩みを投げると、蔵書のハイライトから解決策を返す。PWA と同じ 3 段構え。

    .venv/bin/python ask.py "はなしかたでない悩んでいる"

  1. Gemini でクエリを検索向けに正規化する
       multilingual-e5-small は崩れた口語("はなしかたでない")を解釈できず、
       整った書き言葉に直すと途端に当たるようになる。ここが精度の要。
  2. ローカルの e5 で埋め込み、int8 のベクトル検索で候補を出す
  3. Gemini がハイライトだけを根拠に回答を組み立てる

PWA (docs/index.html) と同じ手順なので、挙動がおかしいときの切り分けに使う。
"""

from __future__ import annotations

import argparse
import sys

from gemini_api import request_with_retry
from search import search

GEN_MODELS = ["gemini-2.5-flash", "gemini-flash-latest",
              "gemini-2.5-flash-lite", "gemini-flash-lite-latest"]

NORMALIZE_PROMPT = """\
次の相談文を、書籍のハイライト検索に使う検索クエリに書き換えてください。

- 話し言葉・誤字・崩れた文法を、自然な書き言葉に直す
- 相談の核心を表す語を 2〜4 語おぎなう（同義語や上位概念）
- 出力は検索クエリ 1 行のみ。説明・記号・鉤括弧は付けない
- 40 字程度に収める

相談: {query}"""

ANSWER_PROMPT = """\
あなたは、相談者本人が過去に読んで線を引いた本のハイライトだけを手がかりに、\
悩みへの具体的な打ち手を示すアシスタントです。

# 絶対に守ること
- 下の「ハイライト」に書かれている内容だけを根拠にする。一般論で埋めない。
- 主張のたびに、根拠にしたハイライトの番号を [1] [3] のように必ず書く。
- 悩みに本当に効くハイライトが無いときは、無理に答えを作らず「今の蔵書には\
ぴったりの箇所が見当たらない」と正直に言い、いちばん近い箇所だけを紹介する。
- 相談者は iPhone の小さい画面で読む。前置き・お世辞・繰り返しは書かない。

# 出力の形
1 行目: 相談の要点を 1 文で言い換える
空行のあと: 打ち手を最大 3 つ。各項目は「**見出し**」で始め、2〜3 文。出典番号つき。
最後: 「今日やること: 」で始まる 1 行。すぐ実行できる小さな行動をひとつだけ。

# 相談
{query}

# ハイライト
{highlights}"""


def generate(prompt: str, max_tokens: int = 2048) -> tuple[str, str]:
    """使えるモデルを順に試す。枠切れ(429)なら次へ。

    既定で思考が有効なモデルは、その分が maxOutputTokens から引かれて
    本文が途中で切れる。だから thinkingBudget=0 で切りたいのだが、
    gemini-*-latest 系はこの引数自体を拒否して 400 を返す。
    まず切る前提で投げ、断られたら思考を許したまま投げ直し、
    代わりに出力枠を広げて切れを防ぐ。
    """
    last = None
    for model in GEN_MODELS:
        for no_think in (True, False):
            config = {"temperature": 0.4,
                      "maxOutputTokens": max_tokens if no_think else max_tokens * 3}
            if no_think:
                config["thinkingConfig"] = {"thinkingBudget": 0}
            try:
                res = request_with_retry(
                    f"models/{model}:generateContent",
                    {"contents": [{"parts": [{"text": prompt}]}],
                     "generationConfig": config},
                    attempts=2, base_delay=3)
            except SystemExit as e:
                last = e
                if no_think and "400" in str(e):
                    continue          # 思考を切れないモデルだった。切らずに再挑戦
                break                 # それ以外は次のモデルへ
            parts = res["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text, model
            break
    raise SystemExit(f"どのモデルでも生成できなかった: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--no-normalize", action="store_true",
                    help="正規化を挟まず生の文で検索する (比較用)")
    ap.add_argument("--search-only", action="store_true",
                    help="回答生成をせず、当たったハイライトだけ見る")
    ap.add_argument("-n", type=int, default=24)
    args = ap.parse_args()
    raw = " ".join(args.query)

    print(f"相談: {raw}")

    if args.no_normalize:
        query = raw
    else:
        query, model = generate(NORMALIZE_PROMPT.format(query=raw), 120)
        query = query.strip().strip('"「」')
        print(f"検索クエリ: {query}   ({model})")

    hits = search(query, args.n)
    if not hits:
        raise SystemExit("該当なし")

    if args.search_only:
        for i, (score, item, book) in enumerate(hits, 1):
            print(f"{i:2d}. [{score:.3f}] {item['t'][:90]}…")
            print(f"     — 『{book['t'][:44]}』\n")
        return

    lines = "\n\n".join(
        f"[{i}] 『{book['t']}』{' ' + book['a'] if book['a'] else ''}\n{item['t']}"
        for i, (_, item, book) in enumerate(hits, 1))
    answer, model = generate(ANSWER_PROMPT.format(query=raw, highlights=lines))

    print(f"\n{'=' * 72}\n{answer}\n{'=' * 72}")
    print(f"({model} / {len(hits)} 件のハイライトから)\n")
    print("引用元:")
    for i, (_, item, book) in enumerate(hits[:8], 1):
        print(f"  [{i}] 『{book['t'][:50]}』{book['a'][:20]}")


if __name__ == "__main__":
    sys.exit(main())
