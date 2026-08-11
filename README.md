# 本に聞く — Kindleハイライト検索

iPhone から「はなしかたでない悩んでいる」のように打つと、これまで読んだ本の
ハイライトから答えを探して解決策を出す。Mac が起動していなくても使える。

- 蔵書: Obsidian の `40_Kindle_Highlights/` から 646 冊 / 13,663 ハイライト
- 置き場所: GitHub Pages（静的）。サーバーは無し
- 検索: 端末の中で完結（ベクトル検索）。ネットに問い合わせるのは文章化だけ

## 仕組み

```
iPhone のホーム画面「本に聞く」
  │
  ├ ① 相談文を整える ────── Gemini API
  │     「はなしかたでない悩んでいる」
  │       → 「話し方 コミュニケーション 表現 改善」
  │     ※ 埋め込みモデルは崩れた口語を解釈できない。ここが精度の要
  │
  ├ ② 意味で検索 ────────── 端末内（multilingual-e5-small）
  │     13,663 件と int8 の内積 → 上位 24 件（1 冊 3 件まで）
  │
  └ ③ 答えを書く ────────── Gemini API
        ハイライトだけを根拠に、出典番号つきで打ち手を 3 つ
```

蔵書データ（`index.enc`, 7MB）は合い言葉で暗号化して置いてある。
GitHub Pages は private リポジトリでも URL 自体は公開されるため、
生のハイライトは載せない。初回に合い言葉を入れると復号され、
以後は端末の IndexedDB に残るので入力は不要。

## 使う（iPhone）

1. Safari で公開 URL を開く
2. 共有 → **ホーム画面に追加**
3. 初回だけ合い言葉を入れる（蔵書 7MB → AI 118MB の順に取り込む）
4. ⚙ から Gemini API キーを登録する
   （[Google AI Studio](https://aistudio.google.com/apikey) で無料取得。
   キーは端末内の localStorage にだけ入る）

2 回目からは開いてすぐ使える。検索そのものはオフラインでも動く
（①③ の文章化だけネットが要る）。

## 作り直す（Mac）

新しく本を読んでハイライトが増えたら、この 4 つを順に実行する。

```bash
cd /Users/home_dir/gemini_cli/tools/kindle_oracle
.venv/bin/python parse_highlights.py    # Obsidian → highlights.jsonl
.venv/bin/python build_index.py         # 埋め込み（約 1.2 分）
KINDLE_ORACLE_PASSPHRASE='合い言葉' .venv/bin/python pack.py
git -C docs add -A && git commit -m "蔵書を更新" && git push
```

iPhone 側は ⚙ →「蔵書データを削除して入れ直す」で新しい版を取り込む。

## 手元で確かめる

```bash
.venv/bin/python search.py "人前で緊張する"          # 検索だけ
.venv/bin/python ask.py "はなしかたでない悩んでいる"  # 正規化→検索→回答
.venv/bin/python ask.py "..." --no-normalize         # 正規化なしと比較
.venv/bin/python diagnose.py                         # 埋め込みの健全性
```

PWA の結果がおかしいときは、まず `search.py` で同じクエリを試す。
ここで正しければ原因はブラウザ側にある。ブラウザの
`window.__kindleOracle.embedQuery(...)` と突き合わせると切り分けられる。

## 触るときの注意

**Mac と iPhone で同じ埋め込みモデルを使うことが大前提。**
どちらか片方だけ変えるとベクトル空間がズレて検索結果が壊れる。
揃える必要があるのは 3 つ。

| | Mac (`build_index.py`) | iPhone (`docs/index.html`) |
|---|---|---|
| モデル | `model/onnx/model_quantized.onnx` | `EMBED_MODEL` + `EMBED_DTYPE='q8'` |
| 次元 | `DIM = 384` | `DIM = 384` |
| 接頭辞 | `passage: ` | `query: ` |

`passage:` / `query:` は e5 系の作法で、外すと精度が大きく落ちる。

## 経緯（同じ轍を踏まないために）

- **Gemini の埋め込み API は使えなかった。** 無料枠が
  `EmbedContentRequestsPerDay = 1000`（バッチ内の 1 件が 1 リクエスト換算）で、
  13,663 件には 14 日かかる。分あたり制限ではない。
- **代わりにローカルの multilingual-e5-small を使う。** 無料・無制限で 1.2 分。
  ただし「はなしかたでない」のような崩れた口語は解釈できず、上位が無関係な本で
  埋まった。関連文と無関係文のスコア差が +0.027 しかなかった。
- **量子化(q8)は無罪。** fp32 と比べても差は同じ +0.0275、ベクトルの一致は
  0.992〜0.997。モデルを大きくするより、クエリを整えるほうが効いた。
- **Gemini でクエリを正規化したら解決した。** 整った書き言葉にすれば
  e5-small でも上位が話し方の本で埋まる。正規化が失敗（枠切れ）しても
  生の文のまま検索は続く。
- **flash 系は `thinkingBudget: 0` が要る。** 既定で思考が有効で、その分が
  `maxOutputTokens` から引かれ、回答が途中で切れる。
