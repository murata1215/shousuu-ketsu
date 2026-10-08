# 嘘八百万 —少数決— 観戦ビューア

本戦（AI同士の試合）の記録をブラウザで読める、神視点専用のビューア。
`logs/llm/` の記録を読み取り専用で読む（記録は書き換えない）。

> dangou-card・gentei-jankenのビューアと違い、プレイヤー視点の秘匿表示は作っていない
> （本作は全員AIで観戦者しかいないため、CLAUDE.md「すべて神視点」の方針）。

## 開発用の手動起動

```bash
uv run python -m viewer.server

# ポート指定
VIEWER_PORT=8899 uv run python -m viewer.server

# 認証トークン付き
VIEWER_TOKEN=secret123 uv run python -m viewer.server
```

## 公開運用（port 9028 を予定）

dangou-card・gentei-jankenと同じsystemd user service方式。手順は
[運用マニュアル](../doc/viewer_operations.md)を参照。

## 環境変数

| 変数名 | 既定値 | 説明 |
|---|---|---|
| `VIEWER_HOST` | `127.0.0.1` | バインドアドレス |
| `VIEWER_PORT` | `9028` | ポート |
| `VIEWER_ROOT_PATH` | (空) | サブパス配信用（FastAPI root_path） |
| `VIEWER_LOG_ROOT` | `logs/llm` | ログディレクトリ |
| `VIEWER_TOKEN` | (空) | 簡易認証トークン（未設定=認証なし、`X-Viewer-Token`ヘッダか`?token=`で照合） |

## ルーティング

| パス | 内容 |
|---|---|
| `/` | 試合一覧（席の割り当てがある試合だけ。Bot試合・動作確認は出さない） |
| `/watch` | 試合のページ本体（タブ式: 概要／ラウンド／契約／席）。相対パス依存のため末尾スラッシュ無し |
| `/static/*` | 静的ファイル（CSS・感情画像） |
| `/api/*` | 試合データAPI |

## API

- `GET /api/games` — 試合一覧
- `GET /api/games/{game_id}/overview` — 席・モデル・借入額・最終資産・順位、12ラウンドの流れ、資産推移、事故件数
- `GET /api/games/{game_id}/rounds/{round_num}` — ラウンドの交渉時系列・内心メモ・投票公開・契約決済・利息・事故
- `GET /api/games/{game_id}/contracts` — 契約の一覧（成立順・義務・結果／提案だけで不成立の分）
- `GET /api/games/{game_id}/seats/{pid}` — 席のページ（票・発言・契約・資産推移・振り返り）

発言本文・内心メモ・振り返りなど自由記述は、応答直前に`viewer/redact.py`を通し
メールアドレス・実行環境のパス・利用者名を伏せる。送った指示文（prompt）の全文は
どのAPIにも含まれない（`viewer/log_index.py`が作る索引が構造的に持たない）。

## 索引（LLM呼び出し索引）

`logs/llm/{game_id}_llm_calls.jsonl`は本戦で約40MBあり、prompt全文を含む。
初回アクセス時に`logs/llm/viewer_index/{game_id}.json`へ軽い索引
（内心メモ・emotion・エラー種別だけ）を一度だけ作って再利用する
（mtime+sizeが変わらない限り再生成しない）。索引ディレクトリは`logs/`の下
にあるため`.gitignore`対象（コミットされない）。

手動で索引だけ作る場合:

```bash
uv run python -m viewer.log_index --game-id l12r12_2002
```

## 場面リンク

`/watch` にURLパラメータを付けると、操作なしで特定の試合・ラウンド・席・手番・
契約を開いた状態で表示できる（dangou-card viewer/static/index.htmlの場面リンクと
同じ`history.replaceState`方式。アドレスバーをコピーすれば「いま見ている場面」の
リンクになる）。

| パラメータ | 例 | 内容 |
|---|---|---|
| `game` | `l12r12_2002` | 試合ID |
| `tab` | `overview`/`round`/`contracts`/`seat` | 開くタブ（省略時は概要） |
| `round` | `9` | ラウンドタブをこのラウンドにする |
| `seat` | `P08` | ラウンドタブでの絞り込み、または席タブで開く席 |
| `turn` | `3` | 交渉のこの手番の行動カードを光らせる |
| `phase` | `vote` | 投票公開を光らせる（`round`タブ、`turn`省略時） |
| `contract` | `C_1CZE6AD3` | 契約タブ・ラウンドタブで、この契約の行を光らせる |

### 例

```
# R9の投票公開
http://127.0.0.1:9028/watch?game=l12r12_2002&tab=round&round=9&phase=vote

# R12のOpus 5(P08)とHaiku(P03)の契約
http://127.0.0.1:9028/watch?game=l12r12_2002&tab=contracts&contract=C_1CZE6AD3
```

各行動カード・契約行の🔗ボタンで、いま見ている場面のURLをクリップボードへコピーできる。

## 感情画像

dangou-cardリポジトリで既に公開されている`viewer/static/emotions/`の42枚
（6ベンダ×7感情）をそのままコピーして使っている。表に無い感情（「笑」等）は
絵文字にフォールバックする。
