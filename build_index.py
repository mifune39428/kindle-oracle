#!/usr/bin/env python3
"""highlights.jsonl をローカルの multilingual-e5-small で埋め込み、
int8 量子化したベクトルインデックスを作る。

iPhone 側 (Transformers.js) と Mac 側でベクトル空間を一致させる必要があるため、
**同じ ONNX ファイル (onnx/model_quantized.onnx) を両方で使う**。
モデルや dtype を片方だけ変えると検索結果が壊れる。

e5 系は接頭辞が必須:
    文書側 = "passage: ...", 質問側 = "query: ..."
PWA 側の QUERY_PREFIX と必ず対で変えること。

本のハイライトに加えて、自分の記事（personal_ai/corpus/articles）も同じ索引に入れる。
出典テーブル(books)は本と記事の両方を持ち、記事には url と日付が付く（k="a"）。

出力:
  vectors.i8   int8 × 件数 × 384
  meta.json    本文・書名・著者・ASIN・location（記事は url・date・見出し）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

HERE = Path(__file__).parent
MODEL_DIR = HERE / "model"
ONNX_PATH = MODEL_DIR / "onnx" / "model_quantized.onnx"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"

DIM = 384
MAX_LEN = 512
PASSAGE_PREFIX = "passage: "


def passage_text(rec: dict) -> str:
    """埋め込みに掛ける文。自分で書いたメモは本文と同じくらい手がかりになるので
    ハイライトに続けて混ぜる。表示は meta.json 側で分けて持つ。

    記事は塊だけだと何の話か分からなくなるので、題名と見出しを前に付ける。"""
    if rec.get("kind") == "article":
        head = rec.get("heading") or ""
        return f"{rec['title']}｜{head} {rec['text']}" if head else f"{rec['title']} {rec['text']}"
    note = rec.get("note")
    return f"{rec['text']} 【メモ】{note}" if note else rec["text"]


def load_session(threads: int) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(ONNX_PATH), opts, providers=["CPUExecutionProvider"])


def encode_batch(session: ort.InferenceSession, encodings: list) -> np.ndarray:
    """トークン列のバッチを mean pooling して L2 正規化したベクトルにする。"""
    maxlen = max(len(e.ids) for e in encodings)
    n = len(encodings)
    input_ids = np.zeros((n, maxlen), dtype=np.int64)
    attention = np.zeros((n, maxlen), dtype=np.int64)
    for i, e in enumerate(encodings):
        L = len(e.ids)
        input_ids[i, :L] = e.ids
        attention[i, :L] = e.attention_mask

    out = session.run(["last_hidden_state"], {
        "input_ids": input_ids,
        "attention_mask": attention,
        "token_type_ids": np.zeros_like(input_ids),
    })[0]

    mask = attention[:, :, None].astype(np.float32)
    summed = (out * mask).sum(axis=1)
    counts = np.maximum(mask.sum(axis=1), 1e-9)
    mean = summed / counts
    norms = np.maximum(np.linalg.norm(mean, axis=1, keepdims=True), 1e-12)
    return mean / norms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--threads", type=int, default=0, help="0 なら onnxruntime に任せる")
    ap.add_argument("--limit", type=int, help="先頭 N 件だけ (動作確認用)")
    ap.add_argument("--no-articles", action="store_true",
                    help="自分の記事を入れず、本のハイライトだけで作る")
    args = ap.parse_args()

    if not ONNX_PATH.exists():
        raise SystemExit(f"モデルが無い: {ONNX_PATH}\n"
                         "先に fetch_model.py を実行すること。")

    records = [json.loads(l) for l in
               (HERE / "highlights.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.limit:
        records = records[:args.limit]

    articles: list[dict] = []
    if not args.no_articles:
        # 塊の切り方は personal_ai 側が持っている。ここに写すと片方だけ直って食い違うので借りる。
        # **記事だけ**を借りること。Apple のメモとメール・予定は外に出さない。
        personal_ai = os.path.join(os.path.dirname(str(HERE)), "personal_ai")
        if personal_ai not in sys.path:
            sys.path.insert(0, personal_ai)
        import passages
        articles = passages.load_articles()
        if args.limit:
            articles = articles[:args.limit]
        print(f"本のハイライト {len(records)} 件＋自分の記事 {len(articles)} 塊")
    records += articles

    tok = Tokenizer.from_file(str(TOKENIZER_PATH))
    tok.enable_truncation(max_length=MAX_LEN)
    session = load_session(args.threads)

    print(f"{len(records)} 件を {DIM} 次元に埋め込む")
    t0 = time.time()

    encodings = tok.encode_batch([PASSAGE_PREFIX + passage_text(r) for r in records])

    # パディングを減らすため長さ順に処理する。結果は元の並びに戻す。
    order = sorted(range(len(encodings)), key=lambda i: len(encodings[i].ids))
    vectors = np.zeros((len(records), DIM), dtype=np.float32)

    for start in range(0, len(order), args.batch):
        idxs = order[start:start + args.batch]
        vecs = encode_batch(session, [encodings[i] for i in idxs])
        for slot, i in enumerate(idxs):
            vectors[i] = vecs[slot]
        done = start + len(idxs)
        if done % (args.batch * 20) == 0 or done == len(order):
            el = time.time() - t0
            print(f"  {done}/{len(order)}  {done / el:.0f} 件/秒  "
                  f"残り約 {(len(order) - done) / max(done / el, 1e-9) / 60:.1f} 分",
                  flush=True)

    # 正規化済みベクトルを int8 に落とす (127 倍して丸め)
    quant = np.clip(np.rint(vectors * 127), -127, 127).astype(np.int8)
    (HERE / "vectors.i8").write_bytes(quant.tobytes())

    # 書籍情報は別テーブルにして重複を排除する
    books: list[dict] = []
    book_idx: dict[str, int] = {}
    items = []
    for r in records:
        is_article = r.get("kind") == "article"
        # 記事は url が一意。本は ASIN（無ければ書名）
        key = (r.get("url") or r["title"]) if is_article else (r["asin"] or r["title"])
        if key not in book_idx:
            book_idx[key] = len(books)
            src = {"t": r["title"], "a": r["author"], "s": r.get("asin") or ""}
            if is_article:
                src["k"] = "a"                    # 出し分け用（a = 自分の記事）
                src["u"] = r.get("url") or ""
                src["d"] = r.get("date") or ""
            books.append(src)
        item = {"t": r["text"], "b": book_idx[key], "l": r["loc"]}
        if r.get("note"):
            item["n"] = r["note"]
        if is_article and r.get("heading"):
            item["h"] = r["heading"]
        items.append(item)

    notes = sum(1 for it in items if "n" in it)
    n_articles = sum(1 for b in books if b.get("k") == "a")
    meta = {"dim": DIM, "count": len(records), "model": "multilingual-e5-small-q8",
            "books": books, "items": items}
    (HERE / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"\nvectors.i8 : {(HERE / 'vectors.i8').stat().st_size / 1e6:.1f} MB")
    print(f"meta.json  : {(HERE / 'meta.json').stat().st_size / 1e6:.1f} MB "
          f"(本 {len(books) - n_articles} 冊 / 記事 {n_articles} 本 / メモ付き {notes} 件)")
    print(f"所要        : {(time.time() - t0) / 60:.1f} 分")


if __name__ == "__main__":
    main()
