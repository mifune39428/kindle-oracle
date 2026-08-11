#!/usr/bin/env python3
"""Obsidian の Kindle ハイライト md を 1 件 1 レコードの JSONL に変換する。

入力: 40_Kindle_Highlights/*.md (Obsidian Kindle Highlights プラグイン形式)
出力: highlights.jsonl

Kindle のハイライトは同じ location で複数行に分割されることがある
(引用の途中で切れて次の行に続く)。同一 location の連続断片は 1 件に結合する。
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

VAULT_DIR = Path.home() / "Antigravity/obsidian/40_Kindle_Highlights"

# 例: 本文テキスト — location: [146](kindle://book?...&location=146) ^ref-28364
HIGHLIGHT_RE = re.compile(
    r"^(?P<text>.*?)\s+—\s+location:\s+\[(?P<loc>\d+)\]\((?P<url>[^)]+)\)"
    r"(?:\s+\^ref-(?P<ref>\w+))?\s*$"
)

FRONTMATTER_KEYS = {
    "kindle-title": "title",
    "kindle-author": "author",
    "kindle-asin": "asin",
    "kindle-bookImageUrl": "cover",
}

# 本文として意味をなさない最低文字数。これ未満は前後の断片と結合されなければ捨てる。
MIN_CHARS = 12


def parse_frontmatter(lines: list[str]) -> tuple[dict, int]:
    """先頭の YAML frontmatter を読む。戻り値は (メタ情報, 本文開始行)。"""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, i + 1
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in FRONTMATTER_KEYS:
            meta[FRONTMATTER_KEYS[key]] = value.strip().strip("'\"")
    return meta, 0


def normalize(text: str) -> str:
    """全角/半角ゆれをならし、Kindle 特有の空白装飾を落とす。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("　", " ")  # 全角スペース
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_file(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    meta, body_start = parse_frontmatter(lines)

    title = meta.get("title") or path.stem
    author = meta.get("author", "")
    asin = meta.get("asin", "")

    raw: list[tuple[int, str]] = []  # (location, text)
    for line in lines[body_start:]:
        m = HIGHLIGHT_RE.match(line.strip())
        if not m:
            continue
        text = normalize(m.group("text"))
        if not text:
            continue
        raw.append((int(m.group("loc")), text))

    # 同一 location の連続断片を結合する
    merged: list[tuple[int, str]] = []
    for loc, text in raw:
        if merged and merged[-1][0] == loc:
            prev_loc, prev_text = merged[-1]
            merged[-1] = (prev_loc, f"{prev_text}{text}")
        else:
            merged.append((loc, text))

    records = []
    for loc, text in merged:
        if len(text) < MIN_CHARS:
            continue
        records.append(
            {
                "text": text,
                "title": title,
                "author": author,
                "asin": asin,
                "loc": loc,
            }
        )
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", type=Path, default=VAULT_DIR)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "highlights.jsonl")
    args = ap.parse_args()

    files = sorted(args.vault.glob("*.md"))
    if not files:
        raise SystemExit(f"md が見つからない: {args.vault}")

    all_records: list[dict] = []
    seen: set[str] = set()
    dupes = 0
    for path in files:
        for rec in parse_file(path):
            # 同じ本の中の完全重複（再ハイライト）は落とす
            key = f"{rec['asin']}|{rec['text']}"
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            all_records.append(rec)

    for i, rec in enumerate(all_records):
        rec["id"] = i

    with args.out.open("w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    books = len({r["asin"] or r["title"] for r in all_records})
    chars = sum(len(r["text"]) for r in all_records)
    lengths = sorted(len(r["text"]) for r in all_records)
    print(f"ファイル数     : {len(files)}")
    print(f"ハイライト件数 : {len(all_records)} (重複除去 {dupes} 件)")
    print(f"書籍数         : {books}")
    print(f"総文字数       : {chars:,}")
    print(f"平均 / 中央値  : {chars // len(all_records)} / {lengths[len(lengths) // 2]} 文字")
    print(f"最長           : {lengths[-1]:,} 文字")
    print(f"出力           : {args.out}")


if __name__ == "__main__":
    main()
