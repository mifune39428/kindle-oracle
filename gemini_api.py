#!/usr/bin/env python3
"""Gemini API の薄いラッパ。標準ライブラリのみ。

API キーは以下の順で探す:
  1. 環境変数 GEMINI_API_KEY
  2. このディレクトリの .env
  3. 既存ツール dual_draft_poster/.env  (同じキーを使い回す)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://generativelanguage.googleapis.com/v1beta"

# 呼び出し側が「今のペースが速すぎたか」を知るためのカウンタ
RETRY_STATS = {"429": 0}

ENV_CANDIDATES = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / "dual_draft_poster" / ".env",
]


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()
    for path in ENV_CANDIDATES:
        if path.exists():
            value = _parse_env(path).get("GEMINI_API_KEY")
            if value:
                return value
    raise SystemExit(
        "GEMINI_API_KEY が見つからない。環境変数か "
        f"{ENV_CANDIDATES[0]} に設定すること。"
    )


def request(path: str, payload: dict | None = None, method: str = "POST",
            timeout: int = 120) -> dict:
    """Gemini API を叩く。キーはヘッダで送る (URL に載せない)。"""
    url = f"{BASE}/{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-goog-api-key", api_key())
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _retry_delay(body: str) -> float | None:
    """429 本文の RetryInfo.retryDelay ("17s") を秒に変換する。"""
    try:
        err = json.loads(body).get("error", {})
    except json.JSONDecodeError:
        return None
    for d in err.get("details", []):
        raw = d.get("retryDelay")
        if isinstance(raw, str) and raw.endswith("s"):
            try:
                return float(raw[:-1])
            except ValueError:
                pass
    return None


def quota_summary(body: str) -> str:
    """429 本文から、どの割当に当たったのかを 1 行で取り出す。"""
    try:
        err = json.loads(body).get("error", {})
    except json.JSONDecodeError:
        return "(本文を解釈できず)"
    hits = []
    for d in err.get("details", []):
        for v in d.get("violations", []):
            qid = (v.get("quotaId") or "?").replace("PerUserPerProjectPerModel", "")
            hits.append(f"{qid}={v.get('quotaValue')}")
    return ", ".join(hits) or "(violations なし)"


def request_with_retry(path: str, payload: dict, *, attempts: int = 9,
                       base_delay: float = 5.0) -> dict:
    """429/5xx は粘る。429 はサーバの retryDelay を優先し、無ければ指数バックオフ。"""
    for attempt in range(attempts):
        try:
            return request(path, payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            retriable = e.code == 429 or 500 <= e.code < 600
            if not retriable or attempt == attempts - 1:
                raise SystemExit(f"HTTP {e.code}: {body[:400]}") from e
            if e.code == 429:
                RETRY_STATS["429"] += 1
            hinted = _retry_delay(body) if e.code == 429 else None
            # retryDelay はぎりぎりの値なので少し上乗せする
            delay = (hinted + 3) if hinted else base_delay * (2 ** attempt)
            detail = f"  [{quota_summary(body)}]" if e.code == 429 else ""
            print(f"  HTTP {e.code} → {delay:.0f}s 待って再試行 "
                  f"({attempt + 1}/{attempts - 1}){detail}", flush=True)
            time.sleep(delay)
        except urllib.error.URLError as e:
            if attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise SystemExit("再試行が尽きた")


if __name__ == "__main__":
    models = request("models", method="GET").get("models", [])
    embed = [m for m in models
             if "embedContent" in m.get("supportedGenerationMethods", [])]
    print(f"埋め込み対応モデル ({len(embed)}):")
    for m in embed:
        name = m["name"].removeprefix("models/")
        dim = m.get("outputDimension", "?")
        print(f"  {name:38s} dim={dim} in={m.get('inputTokenLimit', '?')}")
    print(f"\n生成モデル (先頭10):")
    gen = [m["name"].removeprefix("models/") for m in models
           if "generateContent" in m.get("supportedGenerationMethods", [])]
    for name in gen[:10]:
        print(f"  {name}")
    print(f"  ... 計 {len(gen)} 個")
