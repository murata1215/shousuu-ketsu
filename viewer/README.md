# 嘘八百万 —少数決— 観戦ビューア

本戦（AI同士の試合）の記録をブラウザで読める、神視点専用のビューア。
`logs/llm_v0_4/`（v0.4/v0.5）・`logs/llm/`（v0.3、旧形式）の記録を読み取り
専用で読む（記録は書き換えない）。

> dangou-card・gentei-jankenのビューアと違い、プレイヤー視点の秘匿表示は作っていない
> （本作は全員AIで観戦者しかいないため、CLAUDE.md「すべて神視点」の方針）。

サイクル4.3でv0.4（ラウンドの中に投票が1〜6回入る二重構造、§1.1）・v0.5
（型Cに山分け pool_share、§6.4）に対応させた。v0.3形式の記録
（`ROUND_OPEN`/`MINORITY_RESOLVED`が存在するもの）は一覧に「旧形式」の札を
付けて残すが、詳細（ラウンド・契約・お金の推移）は返さない。席・モデル・
最終資産・順位だけを見られる（v0.3の詳細な表示はgitタグ`v0.3-season1`の
ビューアで見られる）。

## 開発用の手動起動

```bash
uv run python -m viewer.server

# ポート指定
VIEWER_PORT=8899 uv run python -m viewer.server

# 置き場を指定（カンマ区切り。既定: logs/llm_v0_4,logs/llm）
VIEWER_LOG_ROOTS=logs/llm_v0_4,logs/llm uv run python -m viewer.server

# 認証トークン付き
VIEWER_TOKEN=secret123 uv run python -m viewer.server
```

## 公開運用（port 9028、`shousuu-ketsu-viewer.devrelay.io`で公開中）

dangou-card・gentei-jankenと同じsystemd user service方式。手順は
[運用マニュアル](../doc/viewer_operations.md)を参照。

## 環境変数

| 変数名 | 既定値 | 説明 |
|---|---|---|
| `VIEWER_HOST` | `127.0.0.1` | バインドアドレス |
| `VIEWER_PORT` | `9028` | ポート |
| `VIEWER_ROOT_PATH` | (空) | サブパス配信用（FastAPI root_path） |
| `VIEWER_LOG_ROOTS` | `logs/llm_v0_4,logs/llm` | ログディレクトリ（カンマ区切り、複数指定可。サイクル4.3新設） |
| `VIEWER_LOG_ROOT` | (空) | 旧名（単一指定、互換のため残す。`VIEWER_LOG_ROOTS`未設定時のみ使う） |
| `VIEWER_TOKEN` | (空) | 簡易認証トークン（未設定=認証なし、`X-Viewer-Token`ヘッダか`?token=`で照合） |

## ルーティング

| パス | 内容 |
|---|---|
| `/` | 試合一覧（席の割り当てがある試合だけ。Bot試合・動作確認は出さない。旧形式は「旧形式」の札付きで残す） |
| `/watch` | 試合のページ本体（タブ式: 概要／ラウンド／契約／お金／席／見どころ）。相対パス依存のため末尾スラッシュ無し |
| `/static/*` | 静的ファイル（CSS・感情画像） |
| `/api/*` | 試合データAPI |

## API

- `GET /api/games` — 試合一覧（`format`に`"v0.4"`/`"v0.3"`の札）
- `GET /api/games/{game_id}/overview` — 席・モデル・借入額・最終資産・順位・山の受け取り、
  ラウンド→投票の流れ、資産推移、事故件数、お金の照合結果（`money_reconciled`）。
  旧形式は席・モデル・最終資産・順位だけ（`legacy_notice`付き）
- `GET /api/games/{game_id}/rounds/{round_num}` — 投票（R?V?）ごとの質問・交渉・
  内心メモ・投票公開・事故の目印と、ラウンドの精算（山の支払い・山分けの支払い・利息）
- `GET /api/games/{game_id}/contracts` — 契約の一覧（成立順・義務・結果／山分けは
  当事者全員・割合・効く期間で表示し常に「これから」扱い／提案だけで不成立・形式に
  合わず不成立の分）
- `GET /api/games/{game_id}/money` — 席ごとの現金・借金の推移、お金の出入りの記録、
  記録との照合結果
- `GET /api/games/{game_id}/highlights` — 見どころ一覧（嘘・単独勝ち・型Bの違反・
  山分けの支払い・やり直し/打ち切り）
- `GET /api/games/{game_id}/seats/{pid}` — 席のページ（投票ごとの票・発言・契約・
  資産推移・お金の出入り・振り返り）
- `GET /api/games/{game_id}/events/{line}` — 記録ファイルの行番号から、その場面
  （タブ・ラウンド・投票・巡・席・契約）を解決する（場面への直リンク`event=`用）

発言本文・内心メモ・振り返りなど自由記述は、応答直前に`viewer/redact.py`を通し
メールアドレス・実行環境のパス・利用者名を伏せる。送った指示文（prompt）の全文は
どのAPIにも含まれない（`viewer/log_index.py`が作る索引が構造的に持たない）。

## お金の推移の復元

`viewer/money_trail.py::build_money_trail`が、記録イベント（借入公開・参加費・
延長料・山の受け取り・契約の支払い・送金・返済・利息）だけから現金・借金の
時系列を積み上げる。`engine.config.GameConfig`の既定値は一切参照しない
（CLAUDE.md過去の落とし穴⑦の再発防止）。本番7試合で本人向け財務通知・最終資産
と1円単位で一致することを確認済み（`money_reconciled`/`reconciled`フラグ、
`tests/test_viewer_real_match_v0_4.py`）。

## 索引（LLM呼び出し索引）

`logs/llm_v0_4/{game_id}_llm_calls.jsonl`は本戦で約10〜40MBあり、prompt全文を含む。
初回アクセス時に`{置き場}/viewer_index/{game_id}.json`へ軽い索引
（内心メモ・emotion・vote_num・エラー種別だけ）を一度だけ作って再利用する
（mtime+size+索引の形式版`INDEX_VERSION`が変わらない限り再生成しない）。索引
ディレクトリは`logs/`の下にあるため`.gitignore`対象（コミットされない）。

手動で索引だけ作る場合:

```bash
uv run python -m viewer.log_index --game-id t12_r4_4501c --log-dir logs/llm_v0_4
```

## 場面リンク

`/watch` にURLパラメータを付けると、操作なしで特定の試合・ラウンド・投票・席・
手番・契約・記録の行を開いた状態で表示できる（dangou-card viewer/static/index.htmlの
場面リンクと同じ`history.replaceState`方式。アドレスバーをコピーすれば「いま見ている
場面」のリンクになる）。

| パラメータ | 例 | 内容 |
|---|---|---|
| `game` | `t12_r4_4501c` | 試合ID |
| `tab` | `overview`/`round`/`contracts`/`money`/`seat`/`highlights` | 開くタブ（省略時は概要） |
| `round` | `3` | ラウンドタブをこのラウンドにする |
| `vote` | `3` | ラウンドタブをこの投票（R?V?のV）にする（サイクル4.3新設） |
| `player` | `P06` | ラウンドタブでの絞り込み、席タブ・お金タブで開く席（古い`seat=`も受ける） |
| `turn` | `4` | 交渉のこの手番の行動カードを光らせる |
| `phase` | `vote` | 票公開を光らせる（`round`タブ、`turn`省略時） |
| `contract` | `C_4YVBTCF4` | 契約タブ・ラウンドタブで、この契約の行を光らせる |
| `event` | `1099` | 記録ファイル（`{game_id}_events.jsonl`）のこの行番号の場面を開く（サイクル4.3新設） |

`event=`は他の指定より優先して場面を解決する（`/api/games/{game_id}/events/{line}`で
ラウンド・投票・巡・席・契約を引いてから各タブに反映する）。動画の台本づくりで、
記録の行番号から直接その場面へ飛べる。

### 例

```
# R3V3のP06のDM（記録の行番号から直接開く）
http://127.0.0.1:9028/watch?game=t12_r4_4501c&round=3&vote=3&player=P06&event=1099

# R1の山分けの支払いの一覧（見どころタブから飛ぶのと同じ場面）
http://127.0.0.1:9028/watch?game=t12_r4_4501c&tab=highlights
```

各行動カード・契約行・お金の出入りの行の🔗ボタンで、いま見ている場面のURLを
クリップボードへコピーできる。

## 感情画像

dangou-cardリポジトリで既に公開されている`viewer/static/emotions/`の42枚
（6ベンダ×7感情）をそのままコピーして使っている。表に無い感情（「笑」等）は
絵文字にフォールバックする。
