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

# 実際に叩いて確かめた順。バージョン固定名(gemini-2.5-*)は ListModels に
# 出てくるのに呼ぶと 404 "no longer available to new users" になるものがあり、
# 残っているものも 503 になりやすい。-latest 系だけが安定して通る。
GEN_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest",
              "gemini-2.5-flash", "gemini-pro-latest"]

NORMALIZE_PROMPT = """\
次の相談文を、書籍のハイライト検索に使う検索クエリに書き換えてください。

- 話し言葉・誤字・崩れた文法を、自然な書き言葉に直す
- 相談の核心を表す語を 2〜4 語おぎなう（同義語や上位概念）
- 出力は検索クエリ 1 行のみ。説明・記号・鉤括弧は付けない
- 40 字程度に収める

相談: {query}"""

ANSWER_PROMPT = """\
あなたは、相談者の話を聞いて一緒に考える相談相手です。
材料は2つだけ。相談者が過去に読んで線を引いた本の一節と、相談者自身が過去に書いた記事です。

# あなたの仕事
材料を並べ直すことではありません。材料を組み合わせて相談者の状況に当てはめ、\
「だから、あなたはこうするといい」まで考え抜くことです。

# 守ること
- 手がかりを1つずつ言い換えて並べない。複数の手がかりをつないで、1つの考えにまとめる。
- 相談文に書かれた状況（何に困っているか・何を気にしているか）を必ず使って具体化する。\
誰にでも言える助言にしない。
- 行動は「何を・どれくらい・いつ」まで書く。「意識する」「大切にする」で終わらせない。
- どの手がかりから考えたかは [1] [3] のように番号で示す。つなぎの推論や具体化の部分には番号は要らない。
- 手がかりに無い事実・数字・本の言葉を作らない。考えを補うのはよいが、引用を捏造しない。
- 「自分の記事」は相談者自身が書いたもの。日付があれば「2026-05-02 のあなた自身も〜と書いている」\
のように、過去の自分の言葉として返す。
- 相談に効く手がかりがほとんど無いときは、その旨を1行で言ってから、近い手がかりで考えられる範囲だけ答える。
- iPhone の小さい画面で読む。前置き・お世辞・同じことの繰り返しは書かない。

# 出力の形
**見立て**
なぜいまそうなっているのかを2〜3文で。手がかりを組み合わせて説明する。

**打ち手**
3つまで。各項目は「1. 見出し」の1行で始め、続けて次の2行を書く。
やること: 具体的な行動（何を・どれくらい・いつ）
なぜ効くか: 手がかりから考えた理由（番号つき）

**今日やること**
5分でできる行動を1つだけ、1行で。

相談文だけでは状況が分からず、答えが大きく変わりそうなときだけ、最後に次を足す。
**聞きたいこと**
1つだけ、1行で。

# 相談
{query}

# 手がかり
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
                if no_think and "HTTP 400" in str(e):
                    continue          # 思考を切れないモデルだった。切らずに再挑戦
                break                 # 404/429/503 などは次のモデルへ
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

    # 自分で書いたメモは本文より本人の関心に近いので、根拠として明示する
    lines = "\n\n".join(
        (f"[{i}] 自分の記事「{book['t']}」{' ' + book['d'] if book.get('d') else ''}（{book['a']}）\n{item['t']}"
         if book.get('k') == 'a' else
         f"[{i}] 『{book['t']}』{' ' + book['a'] if book['a'] else ''}\n{item['t']}")
        + (f"\n（このとき自分で書いたメモ）{item['n']}" if item.get("n") else "")
        for i, (_, item, book) in enumerate(hits, 1))
    answer, model = generate(ANSWER_PROMPT.format(query=raw, highlights=lines), 3000)

    print(f"\n{'=' * 72}\n{answer}\n{'=' * 72}")
    print(f"({model} / {len(hits)} 件のハイライトから)\n")
    print("引用元:")
    for i, (_, item, book) in enumerate(hits[:8], 1):
        print(f"  [{i}] 『{book['t'][:50]}』{book['a'][:20]}")


if __name__ == "__main__":
    sys.exit(main())
