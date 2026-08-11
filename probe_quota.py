#!/usr/bin/env python3
"""429 の中身を読んで、埋め込みモデルの実際の割当量を確かめる。

Gemini は 429 の error.details[].violations[] に quotaId と quotaValue を
入れてくるので、RPM(分あたり) なのか RPD(日あたり) なのかを判別できる。
RPM なら待てば進む。RPD なら別モデルか別手段に切り替える必要がある。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from gemini_api import BASE, api_key

MODELS = ["gemini-embedding-2", "gemini-embedding-2-preview", "gemini-embedding-001"]


def try_embed(model: str, n: int) -> tuple[int, dict | None]:
    """n 件のバッチを 1 回投げて、HTTP ステータスとエラー本文を返す。"""
    payload = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": f"テスト文 {i}: 話し方の悩み"}]},
                "taskType": "RETRIEVAL_DOCUMENT",
                "outputDimensionality": 768,
            }
            for i in range(n)
        ]
    }
    req = urllib.request.Request(
        f"{BASE}/models/{model}:batchEmbedContents",
        data=json.dumps(payload).encode(), method="POST")
    req.add_header("x-goog-api-key", api_key())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            return r.status, None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def show_quota(err: dict | None) -> None:
    if not err:
        return
    detail = err.get("error", {})
    print(f"    message: {detail.get('message', '')[:160]}")
    for d in detail.get("details", []):
        for v in d.get("violations", []):
            print(f"    quotaId    : {v.get('quotaId')}")
            print(f"    quotaValue : {v.get('quotaValue')}")
            print(f"    metric     : {v.get('quotaMetric', '')}")
        if d.get("@type", "").endswith("RetryInfo"):
            print(f"    retryDelay : {d.get('retryDelay')}")


def main() -> None:
    for model in MODELS:
        print(f"\n=== {model} ===")
        status, err = try_embed(model, 50)
        print(f"  50件バッチ → HTTP {status}")
        show_quota(err)
        if status == 429:
            # RPM なら 65 秒空ければ通るはず
            print("  65 秒待って再試行…")
            time.sleep(65)
            status2, err2 = try_embed(model, 50)
            print(f"  再試行 → HTTP {status2}")
            if status2 == 200:
                print("  → 分あたり制限(RPM)。間隔を空ければ完走できる。")
            else:
                print("  → 待っても通らない。日あたり制限(RPD)の可能性。")
                show_quota(err2)
        else:
            print("  → 通った")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
