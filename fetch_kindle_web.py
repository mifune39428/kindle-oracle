#!/usr/bin/env python3
"""Amazon の notebook から、ハイライトとメモを直接取ってくる。

    .venv/bin/python fetch_kindle_web.py

Obsidian のプラグイン経由をやめてこちらを使う。プラグインは同期を手で
起こさないと動かず、実際 6 週間分が抜けていた。web を見れば常に最新。

先に一度 kindle_login.py でログインしておくこと。
Cookie は ~/.kindle_oracle_browser にあり、ここでは読み書きしない。

出力は parse_highlights.py と同じ highlights.jsonl。
ただしメモ (note) を持つ点だけ違う。メモは自分の言葉なので検索の手がかりに
なりやすく、埋め込み対象の本文にも混ぜる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PROFILE = Path.home() / ".kindle_oracle_browser"
BASE = "https://read.amazon.co.jp"
BOOK_SEL = ".kp-notebook-library-each-book"
MIN_CHARS = 12          # これ未満のハイライトは拾わない
SCROLL_STABLE = 4       # 冊数が変わらない回数がこれに達したら読み込み完了とみなす


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("　", " ")
    return re.sub(r"\s+", " ", text).strip()


def load_library(page) -> list[dict]:
    """蔵書ペインを最後までスクロールして、全冊の ASIN・書名・著者を集める。"""
    page.goto(f"{BASE}/notebook", wait_until="domcontentloaded")
    try:
        page.wait_for_selector(BOOK_SEL, timeout=60_000)
    except Exception:
        raise SystemExit(
            "本の一覧が出ない。ログインが切れている可能性がある。\n"
            "  .venv/bin/python kindle_login.py  を実行すること。")

    prev, stable = 0, 0
    while stable < SCROLL_STABLE:
        page.evaluate("""() => {
            const el = document.querySelector('#kp-notebook-library');
            if (el) el.scrollTop = el.scrollHeight;
            window.scrollTo(0, document.body.scrollHeight);
        }""")
        page.wait_for_timeout(700)
        n = len(page.query_selector_all(BOOK_SEL))
        stable = stable + 1 if n == prev else 0
        if n != prev:
            print(f"  読み込み中… {n} 冊", flush=True)
        prev = n

    books = []
    for el in page.query_selector_all(BOOK_SEL):
        asin = el.get_attribute("id")
        if not asin:
            continue
        h2 = el.query_selector("h2")
        p = el.query_selector("p")
        author = normalize(p.inner_text()) if p else ""
        # 最後に線を引いた日。前回から変わっていなければ取り直さずに済む。
        stamp = el.query_selector(f"#kp-notebook-annotated-date-{asin}")
        books.append({
            "asin": asin,
            "title": normalize(h2.inner_text()) if h2 else asin,
            # 「著者: 川端康成」から接頭辞を落とす
            "author": re.sub(r"^著者[:：]\s*", "", author),
            "date": (stamp.get_attribute("value") or "") if stamp else "",
        })
    return books


def load_previous(path: Path) -> dict[str, list[dict]]:
    """前回の取得結果を ASIN ごとにまとめて読む。差分取得で使い回す。"""
    out: dict[str, list[dict]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec.pop("id", None)
        out.setdefault(rec.get("asin", ""), []).append(rec)
    return out


def fetch_book(ctx, book: dict, pause: float) -> list[dict]:
    """1 冊分の注釈を、続きページも辿って集める。"""
    url = f"{BASE}/notebook?asin={book['asin']}&contentLimitState="
    out: list[dict] = []

    while url:
        res = ctx.request.get(url, timeout=60_000)
        if res.status != 200:
            print(f"    HTTP {res.status}: {book['title'][:28]}", flush=True)
            break
        soup = BeautifulSoup(res.text(), "lxml")

        for row in soup.select("#kp-notebook-annotations > div"):
            h = row.select_one("#highlight")
            n = row.select_one("#note")
            loc = row.select_one("#kp-annotation-location")
            text = normalize(h.get_text()) if h else ""
            note = normalize(n.get_text()) if n else ""
            if len(text) < MIN_CHARS and not note:
                continue
            rec = {"text": text, "title": book["title"],
                   "author": book["author"], "asin": book["asin"],
                   "loc": int(loc["value"]) if loc and loc.get("value", "").isdigit() else 0}
            if note:
                rec["note"] = note
            out.append(rec)

        token = soup.select_one(".kp-notebook-annotations-next-page-start")
        state = soup.select_one("#kp-notebook-content-limit-state")
        tv = token.get("value") if token else None
        if tv:
            sv = state.get("value") if state else ""
            url = (f"{BASE}/notebook?asin={book['asin']}"
                   f"&token={tv}&contentLimitState={sv}")
            time.sleep(pause)
        else:
            url = None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "highlights.jsonl")
    ap.add_argument("--pause", type=float, default=0.35,
                    help="リクエスト間隔の秒数（Amazon を叩きすぎないため）")
    ap.add_argument("--limit", type=int, help="先頭 N 冊だけ（動作確認用）")
    ap.add_argument("--full", action="store_true",
                    help="差分取得をせず、全冊を取り直す")
    ap.add_argument("--state", type=Path, default=HERE / ".kindle_state.json",
                    help="前回の「最後に線を引いた日」を覚えておくファイル")
    args = ap.parse_args()

    if not PROFILE.exists():
        raise SystemExit("先に kindle_login.py でログインすること。")

    t0 = time.time()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=True, locale="ja-JP",
            viewport={"width": 1400, "height": 1000})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("蔵書一覧を読み込む")
        books = load_library(page)
        if args.limit:
            books = books[:args.limit]
        print(f"{len(books)} 冊\n")

        # 前回から「最後に線を引いた日」が動いていない本は取り直さない。
        # 全冊なめると 20 分かかるが、差分なら 1 分で済む。
        prev = {} if args.full else load_previous(args.out)
        prev_state: dict[str, str] = {}
        if not args.full and args.state.exists():
            try:
                prev_state = json.loads(args.state.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        records: list[dict] = []
        seen: set[str] = set()
        empty = reused = 0
        for i, book in enumerate(books, 1):
            asin = book["asin"]
            unchanged = (book["date"] and prev_state.get(asin) == book["date"]
                         and asin in prev)
            if unchanged:
                got = prev[asin]
                reused += 1
            else:
                try:
                    got = fetch_book(ctx, book, args.pause)
                except Exception as e:
                    print(f"    取得に失敗: {book['title'][:28]} ({e})", flush=True)
                    continue
                if not got:
                    empty += 1
            for rec in got:
                key = f"{rec['asin']}|{rec['text']}|{rec.get('note','')}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
            if i % 25 == 0 or i == len(books):
                el = time.time() - t0
                print(f"  [{i}/{len(books)}] {len(records)} 件  "
                      f"残り約 {(len(books) - i) * el / i / 60:.1f} 分", flush=True)
            if not unchanged:
                time.sleep(args.pause)
        ctx.close()

    if not args.limit:
        args.state.write_text(
            json.dumps({b["asin"]: b["date"] for b in books}, ensure_ascii=False),
            encoding="utf-8")

    if not records:
        raise SystemExit("1 件も取れなかった。ログインを確認すること。")

    for i, rec in enumerate(records):
        rec["id"] = i
    with args.out.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    notes = sum(1 for r in records if r.get("note"))
    print(f"\nハイライト : {len(records)} 件（うちメモ付き {notes} 件）")
    print(f"書籍       : {len({r['asin'] for r in records})} 冊"
          f"（変化なしで再利用 {reused} 冊 / ハイライトなし {empty} 冊）")
    print(f"所要       : {(time.time() - t0) / 60:.1f} 分")
    print(f"出力       : {args.out}")


if __name__ == "__main__":
    sys.exit(main())
