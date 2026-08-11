#!/usr/bin/env python3
"""vectors.i8 と meta.json をパスフレーズで暗号化し docs/index.enc を作る。

GitHub Pages は private リポジトリでも公開 URL になるため、
インデックスは暗号化して置く。PWA 側が Web Crypto で復号する。

ファイル形式:
    "KOR1"      4 bytes   マジック
    version     1 byte    = 1
    iterations  4 bytes   LE uint32  (PBKDF2 の反復回数)
    salt       16 bytes
    iv         12 bytes
    ciphertext  残り      AES-256-GCM (認証タグ 16 bytes を末尾に含む)

復号後の平文:
    metaLen     4 bytes   LE uint32
    metaGz      metaLen   gzip された meta.json (UTF-8)
    vectors     残り      int8 × count × dim
"""

from __future__ import annotations

import argparse
import getpass
import gzip
import os
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

MAGIC = b"KOR1"
VERSION = 1
# OWASP の PBKDF2-SHA256 推奨値。iPhone で 1 秒弱。
ITERATIONS = 310_000
HERE = Path(__file__).parent


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "docs" / "index.enc")
    ap.add_argument("--iterations", type=int, default=ITERATIONS)
    args = ap.parse_args()

    vectors_path = HERE / "vectors.i8"
    meta_path = HERE / "meta.json"
    for p in (vectors_path, meta_path):
        if not p.exists():
            raise SystemExit(f"{p.name} が無い。先に build_index.py を完走させること。")

    # パスフレーズは環境変数か対話入力。引数では受けない (シェル履歴に残るため)
    passphrase = os.getenv("KINDLE_ORACLE_PASSPHRASE")
    if passphrase:
        print("パスフレーズを環境変数から読んだ")
    else:
        if not sys.stdin.isatty():
            raise SystemExit(
                "パスフレーズが要る。対話実行するか "
                "KINDLE_ORACLE_PASSPHRASE を設定すること。")
        passphrase = getpass.getpass("合い言葉を決めてください: ")
        again = getpass.getpass("もう一度: ")
        if passphrase != again:
            raise SystemExit("一致しない")
    if len(passphrase) < 6:
        raise SystemExit("短すぎる。6 文字以上にすること。")

    meta_raw = meta_path.read_bytes()
    vectors = vectors_path.read_bytes()
    meta_gz = gzip.compress(meta_raw, 9)

    plaintext = struct.pack("<I", len(meta_gz)) + meta_gz + vectors

    salt = os.urandom(16)
    iv = os.urandom(12)
    key = derive_key(passphrase, salt, args.iterations)
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)

    blob = (MAGIC + bytes([VERSION]) + struct.pack("<I", args.iterations)
            + salt + iv + ciphertext)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)

    print(f"meta.json  : {len(meta_raw) / 1e6:.2f} MB → gzip {len(meta_gz) / 1e6:.2f} MB")
    print(f"vectors.i8 : {len(vectors) / 1e6:.2f} MB")
    print(f"出力       : {args.out}  ({len(blob) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
