# CLAUDE.md

嘘八百万 —少数決—（shousuu-ketsu）のルールエンジン実装。

## ドキュメント

- 現行の仕様書: `doc/uso8000000_shousuu_ketsu_spec_v0_4_2.md`（v0.4.2。`doc/..._v0_1.md`・`doc/..._v0_2.md`・`doc/..._v0_3.md`・`doc/..._v0_4.md`・`doc/..._v0_4_1.md` は履歴として残す。変更しない）。v0.4.2はv0.4.1からルールの変更はなく、サイクル4.1の結果を反映した書き直し（§11.7）
- 流用調査レポート: `doc/analysis/reuse_investigation.md`（サイクル0.1。`~/dangou-card`/`~/gentei-janken` からの流用方針・部品表・優劣・仕様書あいまい点16件・段取り案）
- 受け入れテスト対応表: `doc/analysis/acceptance_v0_4.md`（サイクル4.0・4.1。v0.4.1 §12.3の54件とpytest関数の対応）
- Bot検証レポート（v0.4）: `doc/analysis/bot_simulation_report_v0_4.md`（サイクル4.1。V1〜V10・机上計算との比較・暫定値を変えた場合の比較）

## v0.4: 「ラウンド」と「投票」は別の単位（サイクル4.0）

v0.3まで `round_num` と呼んでいたもの（投票1回）は、v0.4では**「投票」（vote、`vote_num`、1〜6）**である。
v0.4の**「ラウンド」**（`round_num`、1〜4）は、12人全員が参加費を出してから山の行き先が決まるまでの
1回の勝負で、中に投票が1〜6回入る（仕様書v0.4 §1.1）。`engine/` 内で新たに「ラウンド＝投票1回」の意味で
`round` を使わないこと。精算も「投票の精算」（§7.3、`engine/settlement.py::settle_vote`）と
「ラウンドの精算」（§7.4、`settle_round`）の2段に分かれている。イベントログは `round_num` と
`vote_num` の両方を持つ（`engine/models.py::GameEvent`）。

## 他プロジェクトの扱い

- `~/dangou-card` と `~/gentei-janken` は**読むだけ**。ファイルも git も一切変更しない。
- 両プロジェクトから部品をコピーする際は、コピー元の HEAD コミットと、作業ツリーに未コミット差分があった場合はその旨を devlog に記録する。

## 秘密情報の扱い

- `.env`・APIキー・トークン・ログは、他プロジェクトからコピーしない・コミットしない・devlogやレポートに書かない。
- キーの名前だけを `.env.example` に記録する（値は空）。実際の値は `.env`（gitignore済み）に書く。
- DevRelay の送信先プロジェクトID（`DEVRELAY_TARGET_PROJECT_ID`）はコード中に既定値を持たない。未設定なら送信前に `AdapterError` で止まる（過去の落とし穴①参照）。

## devlogルール

- 完了時に `doc/devlog/YYYY-MM-DD_HHMMSS.md` を1つ新規作成する（時刻は `TZ=Asia/Tokyo date '+%Y-%m-%d_%H%M%S'` の出力を使う）。
- 冒頭は `# YYYY-MM-DD HH:MM JST ｜ サイクルX.Y: 見出し`。本文は 要求／実行／検証／発見 を平文3〜10行。数値は具体値で書く。
- `doc/devlog/INDEX.md` の末尾に1行だけ追記する（`- YYYY-MM-DD_HHMMSS | サイクルX.Y | 見出し | 1行要約`）。`doc/devlog/` の一括catは禁止。

## テストの実行方法

```
uv sync
uv run pytest -q
```

## 過去の落とし穴（gentei-janken / dangou-card での実例）

1. **DevRelay 宛先 cuid の移植ミス**（gentei-janken サイクル2.7）: dangou-card からの移植時、`DEFAULT_TARGET_PROJECT_ID` が移植元の cuid のまま置き換え忘れられ、実行すると他プロジェクト宛にDevRelayへリクエストが飛ぶ実害があった（一度も実行していなかったため気づかれなかった）。本プロジェクトでは `llm/providers/devrelay_http.py` に既定値を持たせず、`DEVRELAY_TARGET_PROJECT_ID` 環境変数が未設定なら送信前に `AdapterError` で止まる設計にしている（`tests/test_devrelay_http.py` の `test_missing_target_project_id_raises_adapter_error` / `test_no_hardcoded_project_cuid_in_source` で固定）。
2. **通信ライブラリの既知の不具合（httpx/httpx2）**: Anthropic SDK が内部で `httpx2` を使っており、`httpx.Timeout(...)` を渡すと `TypeError: httpx.Timeout is from the httpx package, but this SDK uses httpx2` になる。`pyproject.toml` で `httpx>=0.28.1` を直接依存に明記し、タイムアウトは `httpx.Timeout(...)` ではなく素の秒数（float）で渡す（`llm/adapters.py` に対策済み。`tests/test_adapters.py` の該当テストで固定）。
3. **見た目だけ移して機能が置き去り**（gentei-janken サイクル2.30）: CSSや定数だけ先に移植し、対応する機能実装が追いつかず孤児コードが残った例（`final-reflection` CSSが孤児として残存）。移植は「機能単位」で行い、見た目や定数だけを先行させない。
4. **同型バグの片側だけ直す事故**（gentei-janken サイクル2.33）: プロンプト生成関数の一部でだけ `"json"` という語が指示文から抜け、該当プロバイダ（openai_compat系）だけ400エラーになった。同じ構造の関数が複数ある場合は、修正時に全数を検査する。
5. **長い処理は裏で走らせず、1本ずつ表で回す**（shousuu-ketsu サイクル1.3）: Bot検証（8条件×1,000試合）のような時間のかかる処理は、バックグラウンド実行や並列実行にすると進捗も失敗も見えず、「同じコマンドを繰り返して待つ」事故につながる。`--scenario` を1つずつ指定してフォアグラウンドで1本ずつ回し、完了を確認してから次に進む。
6. **テストデータ・見本記録に実在のアドレス・利用者名・パスを使わない**（shousuu-ketsu サイクル3.0）: ビューアの単体テスト・fixtureの見本記録に、作業用の実在メールアドレスと実在の実行環境パス（利用者名入り）がそのまま書かれ、公開リポジトリにpushされた。テストデータは必ず架空の値（`example.com`のアドレス、架空のユーザー名・パス）で作る。再発防止として `tests/test_no_real_contacts.py::test_no_real_email_addresses_in_tracked_files` がgit管理下の全ファイルを機械的に走査する（`example.com`/`example.org`/`example.net`/`noreply@...`/`git@github.com`以外のメールらしき文字列があれば失敗）。
7. **ビューアがエンジンのConfig既定値を直接読むと、ルール変更で過去の記録の表示が静かに狂う**（shousuu-ketsu サイクル4.0）: `viewer/log_parser.py` が `engine.config.GameConfig().entry_fee`（参加費の既定値）を読んで資産推移を復元していたため、v0.4で参加費が10万→100万に変わると、v0.3の記録（`l12r12_2002`等）の表示が1円単位で狂った。しかもイベントログの`GAME_START`にconfig値自体が記録されていないため、この種の不整合はテストで固定していない限り検出できない。直した方針: ビューア側はConfigを参照せず、その試合の実イベント（`ENTRY_FEE_COLLECTED`の`paid+borrowed`）から値を動的に求める。ログに残らない値をビューア側がConfigの既定値で補う設計は避け、イベント自体から導出できる形にする。
8. **本番コードがテストコードを逆輸入する構造を作らない**（shousuu-ketsu サイクル4.0）: v0.3で `sim/scenarios.py`・`scripts/dry_run.py`（本番コード）が `tests/helpers.py::RandomContractAgent`（テストコード）を直接importしていた。テスト専用モジュールを本番の実行パスが握ると、テストの都合（命名・配置）が本番コードの変更を縛る。v0.4で `bots/random_contract_bot.py::RandomContractBot` へ移設し、本番コードからテストコードへの依存をゼロにした。Botやエージェントなど複数箇所から使われる無作為生成ロジックは、最初から `bots/`（本番側）に置く。
9. **ルールの意味が変わる改修では、旧名のモジュール・型を「改名」ではなく「作り直し」で扱う**（shousuu-ketsu サイクル4.0）: v0.3の `engine/minority.py::resolve_minority`（1回の投票=1ラウンドで配当まで行う）は、v0.4では判定（`engine/vote.py::resolve_vote`）と配当（`engine/round.py`）に分離した。関数名を変えずに中身だけ差し替えると、呼び出し側（`sim/counterfactual.py`等）が新旧どちらの契約で呼んでいるか見分けられなくなる。意味が変わるときはモジュール名・関数名も変え、importエラーで旧呼び出し元を機械的に洗い出せるようにする（実際に `grep -rn "engine.minority\|resolve_minority"` で全呼び出し元を洗い出してから着手した）。
10. **公開（public）の記録を作ったら、プレイヤーに渡す情報にも入れる**（shousuu-ketsu サイクル4.1）: サイクル4.0の `engine/game.py` は、契約の成立本数（`TURN_CONTRACTS_ESTABLISHED`）・型B違反者・払いきれなかった者・AUTO COMMITを `visibility="public"` でイベントログには残していたが、`_build_visible_state()`（プレイヤーに渡す辞書）には入れていなかった。`GameEvent.visibility` は宣言タグにすぎず、engineはそれを読み返して`visible_state`の内容を決めているわけではない（`engine/models.py::Visibility`のdocstring参照）。つまり「公開と記録したのにプレイヤーには届かない」はテストで固定していない限り検出できない構造だった。再発防止として `tests/test_public_disclosure.py::test_every_public_event_type_has_a_visible_state_field` が、`tests/test_event_visibility.py::FIXED_VISIBILITY` の public なイベント種別すべてに対応する `visible_state` の項目名が存在することを機械的に確認する。公開のイベントを新設するときは、必ずこのテストの表（`PUBLIC_EVENT_DELIVERY`）にも項目を追加すること。

（この2点は本サイクル0.2の対象範囲外。実装時に再発させないための記録として残す。）
