#!/usr/bin/env python3
"""Amazon に一度だけログインして、その状態を手元に保存する。

    .venv/bin/python kindle_login.py

ブラウザの窓が開くので、そこで Amazon にログインすること。
パスワードはブラウザに直接入れるだけで、このスクリプトも Claude も受け取らない。
ログインが済むとプロファイル（Cookie 一式）が

    ~/.kindle_oracle_browser

に残り、以後 fetch_kindle_web.py が黙って使う。
セッションが切れたらこのコマンドをもう一度実行する。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

NOTEBOOK = "https://read.amazon.co.jp/notebook"
PROFILE = Path.home() / ".kindle_oracle_browser"
# ログイン済みなら必ずある要素。これが出れば notebook に入れている。
READY = ".kp-notebook-library-each-book"
WAIT_MINUTES = 10


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=Path,
                    help="ログイン後の HTML を書き出す (構造を調べる用)")
    args = ap.parse_args()

    PROFILE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(NOTEBOOK, wait_until="domcontentloaded")

        print("ブラウザで Amazon にログインしてください。")
        print("（すでにログイン済みならそのまま進みます）")
        try:
            page.wait_for_selector(READY, timeout=WAIT_MINUTES * 60_000)
        except Exception:
            print(f"\n{WAIT_MINUTES} 分待ちましたが本の一覧が出ませんでした。", file=sys.stderr)
            print("もう一度実行してみてください。", file=sys.stderr)
            ctx.close()
            sys.exit(1)

        books = page.query_selector_all(READY)
        print(f"\nログインできています。本が {len(books)} 冊見えています。")

        if args.dump:
            args.dump.write_text(page.content(), encoding="utf-8")
            print(f"HTML を {args.dump} に書き出しました。")

        print(f"ログイン状態を {PROFILE} に保存しました。")
        print("このウィンドウは閉じて構いません。")
        ctx.close()


if __name__ == "__main__":
    main()
