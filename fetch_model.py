#!/usr/bin/env python3
"""埋め込みモデルを取得する。

iPhone 側の Transformers.js が読むのと同一のファイルを Mac にも置く。
両者が同じ ONNX を使うことが検索精度の前提なので、ここを変えるときは
docs/index.html の EMBED_MODEL / dtype も必ず合わせること。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "Xenova/multilingual-e5-small"
BASE_FILES = [
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
]
WEIGHTS = {
    "q8": "onnx/model_quantized.onnx",   # Transformers.js の既定 dtype
    "fp16": "onnx/model_fp16.onnx",
    "fp32": "onnx/model.onnx",
}
DEST = Path(__file__).parent / "model"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dtype", choices=list(WEIGHTS), default="q8",
                    help="取得する重みの精度")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    DEST.mkdir(exist_ok=True)
    for name in BASE_FILES + [WEIGHTS[args.dtype]]:
        path = Path(hf_hub_download(args.repo, name, local_dir=str(DEST)))
        print(f"  {name:32s} {path.stat().st_size / 1e6:7.1f} MB")
    print(f"\n{args.repo} ({args.dtype}) を {DEST} に配置した")


if __name__ == "__main__":
    main()
