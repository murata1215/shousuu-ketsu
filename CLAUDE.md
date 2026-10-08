# CLAUDE.md

嘘八百万 —少数決—（shousuu-ketsu）のルールエンジン実装。

## ドキュメント

- 現行の仕様書: `doc/uso8000000_shousuu_ketsu_spec_v0_3.md`（`doc/..._v0_1.md`・`doc/..._v0_2.md` は履歴として残す。変更しない）
- 流用調査レポート: `doc/analysis/reuse_investigation.md`（サイクル0.1。`~/dangou-card`/`~/gentei-janken` からの流用方針・部品表・優劣・仕様書あいまい点16件・段取り案）

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

（この2点は本サイクル0.2の対象範囲外。実装時に再発させないための記録として残す。）
