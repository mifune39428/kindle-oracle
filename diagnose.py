#!/usr/bin/env python3
"""ローカル埋め込みの精度が出ない原因を切り分ける。

確かめること:
  A. 同一文どうしの類似度が 1.0 になるか (pooling が壊れていないか)
  B. 明らかに近い文と遠い文でスコアに差がつくか (空間が潰れていないか)
  C. スコア分布の広がり (全部 0.86 台なら異方性で使い物にならない)
  D. 量子化 (q8) と fp32 で結果がどれだけ変わるか
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from build_index import MAX_LEN, TOKENIZER_PATH, encode_batch

HERE = Path(__file__).parent


def embed(texts: list[str], onnx_path: Path) -> np.ndarray:
    tok = Tokenizer.from_file(str(TOKENIZER_PATH))
    tok.enable_truncation(max_length=MAX_LEN)
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(str(onnx_path), opts,
                                providers=["CPUExecutionProvider"])
    return encode_batch(sess, tok.encode_batch(texts))


PROBES = [
    ("query: はなしかたでない悩んでいる", "クエリ(口語ひらがな)"),
    ("query: 話し方が下手で悩んでいる", "クエリ(漢字・整った文)"),
    ("passage: 少々話し方は不器用でも、相手の心を掴み、心を動かすことができる人のほうが、"
     "会話という視点から見るとコミュニケーション上手だ", "◎ 話し方の文"),
    ("passage: ゆっくり丁寧な言葉で話す。声のトーン、音量を上げ過ぎない。", "◎ 話し方の文2"),
    ("passage: 味噌汁は沸騰させると風味が飛ぶので火を止めてから味噌を溶く",
     "× 無関係(料理)"),
    ("passage: 複利は人類最大の発明である。元本にも利息にも利息がつく", "× 無関係(投資)"),
]


def report(name: str, onnx_path: Path) -> np.ndarray:
    print(f"\n{'=' * 68}\n{name}\n{'=' * 68}")
    vecs = embed([p[0] for p in PROBES], onnx_path)
    sim = vecs @ vecs.T

    print("A. 同一文の自己類似度:", f"{sim[0, 0]:.4f}",
          "(1.0 でなければ pooling がおかしい)")

    q_hira = 0
    print(f"\nB. 「{PROBES[q_hira][1]}」から見た各文のスコア:")
    for j, (_, label) in enumerate(PROBES):
        if j == q_hira:
            continue
        print(f"   {sim[q_hira, j]:.4f}  {label}")

    good = max(sim[q_hira, 2], sim[q_hira, 3])
    bad = max(sim[q_hira, 4], sim[q_hira, 5])
    gap = good - bad
    verdict = "良好" if gap > 0.08 else ("かなり苦しい" if gap > 0.03 else "空間が潰れている")
    print(f"\nC. 関連文と無関係文の差: {gap:+.4f} → {verdict}")
    return vecs


def main() -> None:
    q8 = HERE / "model" / "onnx" / "model_quantized.onnx"
    fp32 = HERE / "model" / "onnx" / "model.onnx"

    v_q8 = report("q8 量子化 (model_quantized.onnx) ※現在これを使用中", q8)

    if not fp32.exists():
        print("\nfp32 モデルが無いので比較を省略。"
              "  .venv/bin/python fetch_model.py --fp32  で取得できる。")
        return

    v_fp = report("fp32 (model.onnx)", fp32)

    print(f"\n{'=' * 68}\nD. q8 と fp32 のズレ\n{'=' * 68}")
    for i, (_, label) in enumerate(PROBES):
        cos = float(v_q8[i] @ v_fp[i])
        print(f"   {cos:.4f}  {label}")
    print("\n   1.0 に近いほど量子化の影響が小さい。0.99 を切ると実害が出る。")


if __name__ == "__main__":
    sys.exit(main())
