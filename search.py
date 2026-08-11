#!/usr/bin/env python3
"""コマンドラインからハイライトを検索する。PWA と同じ手順を Mac 上で再現する。

  .venv/bin/python search.py "はなしかたでない悩んでいる"

PWA 側の検索結果がおかしいと感じたときは、まずこれで切り分ける。
ここで正しく出るならモデルとインデックスは健全で、原因はブラウザ側にある。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from tokenizers import Tokenizer

from build_index import (DIM, MAX_LEN, TOKENIZER_PATH, encode_batch,
                         load_session)

HERE = Path(__file__).parent
QUERY_PREFIX = "query: "   # docs/index.html の QUERY_PREFIX と対で揃えること

TOP_K = 24
PER_BOOK = 3
CAND = 200


def load_index() -> tuple[np.ndarray, dict]:
    meta = json.loads((HERE / "meta.json").read_text(encoding="utf-8"))
    raw = np.frombuffer((HERE / "vectors.i8").read_bytes(), dtype=np.int8)
    vectors = raw.reshape(meta["count"], meta["dim"])
    return vectors, meta


def embed_query(text: str) -> np.ndarray:
    tok = Tokenizer.from_file(str(TOKENIZER_PATH))
    tok.enable_truncation(max_length=MAX_LEN)
    session = load_session(0)
    vec = encode_batch(session, tok.encode_batch([QUERY_PREFIX + text]))[0]
    return np.clip(np.rint(vec * 127), -127, 127).astype(np.int8)


def search(query: str, top_k: int = TOP_K) -> list[tuple[float, dict, dict]]:
    vectors, meta = load_index()
    qvec = embed_query(query)
    # int8 のまま内積を取る (PWA 側と同じ計算)
    scores = vectors.astype(np.int32) @ qvec.astype(np.int32)
    cand = np.argsort(-scores)[:CAND]

    per_book: dict[int, int] = {}
    out = []
    for i in cand:
        item = meta["items"][int(i)]
        n = per_book.get(item["b"], 0)
        if n >= PER_BOOK:
            continue
        per_book[item["b"]] = n + 1
        out.append((scores[i] / (127 * 127), item, meta["books"][item["b"]]))
        if len(out) >= top_k:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("-n", type=int, default=8)
    args = ap.parse_args()
    query = " ".join(args.query)

    print(f"Q: {query}\n" + "=" * 72)
    for rank, (score, item, book) in enumerate(search(query, args.n), 1):
        text = item["t"][:100]
        print(f"{rank:2d}. [{score:.3f}] {text}…")
        print(f"     — 『{book['t'][:46]}』{book['a'][:22]}\n")


if __name__ == "__main__":
    main()
