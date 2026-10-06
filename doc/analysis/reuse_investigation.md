# 流用調査（サイクル0.1）

このタスクでは実装しない。`~/dangou-card` と `~/gentei-janken` は読み取りのみで、ファイル・gitともに無変更。

## 0. 前提

- 読める: `~/dangou-card`（HEAD `5b8b16de82e7f325e40169e0dc2bb580dbe7a30e` / 2026-10-05 12:33:50 +0900 / サイクル10.23）、`~/gentei-janken`（HEAD `2cb1e14595875c038e7a415aa175f913bff4d6dc` / 2026-09-30 07:57:23 +0900 / サイクル2.36）
- `shousuu-ketsu` の現状: git未init・remoteなし。`doc/` に仕様書1本（513行）、`CLAUDE.md` は実質空（2バイト）、`.devrelay/` `.devrelay-output/` のみ
- 環境: 両プロジェクト Python 3.12 / uv / pydantic v2 / pytest。`.env` のキー名は両者同一の9件（`CLAUDE_API_KEY` `OPENAI_API_KEY` `GEMINI_API_KEY` `GROK_API_KEY` `KIMI_API_KEY` `DEEPSEEK_API_KEY` `DEVRELAY_URL` `DEVRELAY_TOKEN` `PIXBLOG_API_TOKEN`）。gentei-janken は `[tool.uv] package = false` を採用し dangou-card の死にコード `src/` を持たない → 少数決も `package = false` 側に倣う。`httpx>=0.28.1` の直接依存は必須（SDKが `httpx2` を引く既知バグ）

## 1. 部品表（部品｜流用元｜A/B/C｜直す点｜付属テスト｜行数の目安）

**A = そのままコピー / B = コピーして一部直す / C = 新規**

| 部品 | 流用元（推奨） | 分類 | 直す点 | 付属テスト | 行数 |
|---|---|---|---|---|---|
| LLMアダプタ6社 | gentei `llm/adapters.py` | A | なし（engine非依存） | `test_adapters.py` | 472 |
| モデルレジストリ | gentei `llm/models.py` | A | `DEFAULT_TARGET_PROJECT_ID` 相当のcuidは要差替（下記リスク） | `test_registry_18.py`(dangou) | 626 |
| DR席(DevRelay) | gentei `llm/providers/devrelay_http.py` | B | DevRelay の targetProjectId を shousuu-ketsu のcuidへ（gentei がここで実害を出した） | `test_devrelay_http.py` | 372 |
| 課金計算 | gentei `llm/costing.py` | A | なし（dangouと0行差） | — | 41 |
| 席ごと費用上限 | gentei `llm/game_cost_budget.py` | A | なし（threading.Lock済み） | `test_game_cost_budget.py`(dangou) | 110 |
| LLM呼出ログ | gentei `llm/llm_logger.py` | A | なし（逐次追記+Lock） | — | 196 |
| 定数 | gentei `llm/constants.py` | A | timeout/retry値 | — | 20 |
| JSONスキーマ | gentei `llm/phase2_schema.py` | B | action種別を少数決の7種+vote_commitに差替 | `test_phase2_schema.py`(dangou) | 142 |
| 応答パーサ | dangou `llm/response_parser.py` | B | `_convert_action`/`_validate_type_c_term_shape` を少数決のaction/条件2種へ。多段サルベージ・memory・emotion・final_reflectionはそのまま | `test_response_parser*.py` `test_type_c_parser.py` | 1,158→約600 |
| LLMエージェント | gentei `llm/llm_agent.py` | B | `commit()` をvote提出へ、`choose_loan()` はそのまま使える | `test_llm_agent_memory.py` `test_cot.py` | 424 |
| プロンプト生成 | dangou `llm/prompt_builder.py` | B | 財務ブロック(2種債務・残り枠)・義務ブロック・順位自己通知・AUTO COMMITブロック・契約テンプレは骨格流用。市場/カード/報奨/匿名/倍掛けの描画は全削除 | `test_prompt_*.py` 約12本 | 3,677→約1,200 |
| イベントログ | gentei `engine/events.py` | A | なし | — | 157 |
| 乱数(再現性) | gentei `engine/rng.py` | A | なし | — | 44 |
| PlayerAgent抽象 | dangou `engine/negotiation.py` | B | `commit()` の戻りをVote化 | — | 176→約120 |
| 設定(GameConfig) | dangou `engine/config.py` | B | `num_players=12`/`num_rounds=12`/`loan_min=1,200,000`/`loan_max=10,000,000`/`interest_rate=0.015`/`entry_fee=100,000`/`negotiation_max_turns=10`/`rank_notice_enabled`/`type_c_enabled` はそのまま使える。`late_interest_rate=0.03`・`debt_cap=10,000,000`・`penalty=1,000,000`・`question_model` を追加。市場/賞金/生還現金/報奨/匿名/倍掛け/強制返済の各フィールドは削除 | — | 414→約180 |
| 契約の型(A/B/C) | dangou `engine/models.py` の `Obligation`/`Contract`/`ObligationType`/`ConditionType` | B | 型Bの details を `{"vote":"YES"}` へ、型Cの condition を `minority_side`/`in_minority` の2種へ。**`contract_seq` フィールドを新設**（dangouには存在しない） | `test_contracts.py` `test_type_c_*.py` | 738→約300 |
| 契約の提案→署名 | dangou `engine/contracts.py` の `create_contract`/`sign_contract`/`validate_type_c_details`/`get_active_type_*` | B | 署名完成時に `contract_seq` を付番。`expire_obligations`（脱落失効）は不要＝削除。`can_cancel_contract`/`request_cancel` は仕様書に記載なく要判断 | `test_contracts.py` `test_contract_terms_validation.py` | 830→約500 |
| contract_id の受信者表示 | dangou `prompt_builder._render_pending_contract_block` | A | なし（提案中契約のid提示パターンをそのまま） | `test_prompt_pending_contracts.py` | 120 |
| 型B監査 | dangou `engine/contracts.audit_type_b` | B | 判定対象をカード/市場から投票先へ。罰を即脱落→違約金100万 | `test_contracts.py` | 50 |
| 型C条件判定 | dangou `engine/contracts.evaluate_type_c_condition` | B | 条件を2種へ。少数派なしのラウンドは全不成立 | `test_type_c_conditional.py` | 57→約60 |
| **支払い執行** | dangou `engine/contracts.execute_type_a_atomic` | **C** | atomic全額払い＋不足で脱落 → `contract_seq`順の部分払い＋不足は借金。**設計が別物**。参考に留める | 新規 | 新規 約200 |
| **借金・現金操作** | dangou `engine/player.py` | **C** | 単一 `debt_balance` + `free_cash=max(0,cash-debt)` → 2種債務(1.5%/3%)＋`debt_cap`＋`remaining_credit`。`spendable_cash` の意味が変わる（transferは §3.2 で現金の範囲内） | 新規（`assets_ranking`/`AssetRank` はA流用） | 408→新規 約250 |
| **Finance(利息)** | dangou `engine/finance.py` | **C** | 2種の債務に別利率で複利・切り上げ、同ラウンド発生分にも利息。生還判定・自動返済は削除 | 新規 | 175→新規 約120 |
| **Settlement** | dangou `engine/settlement.py` | **C** | §7.1 の8手順（Reveal→配当→型B監査→型C判定→上限固定→`contract_seq`順執行→借金確定→公示）。市場解決・倍掛け・高騰は全削除 | 新規 | 600→新規 約300 |
| **少数決の判定・配当・持ち越し** | — | **C** | 完全新規（6対6/12対0、持ち越し、R12消滅、切り捨て） | 新規 | 新規 約120 |
| **質問生成** | — | **C** | 完全新規。`question_model` 1回呼び・12問・40字・禁止語・完全一致重複・予備リスト・履歴ファイル・`--questions` | 新規 | 新規 約250 |
| 自動代行 | dangou `engine/autocommit.py` | B | 合法手の列挙（市場×カード）→ 型B指定からYES/NO決定、矛盾/未指定はseed由来乱数。構造は流用 | `test_autocommit.py` | 181→約80 |
| 交渉フェイズ | dangou `engine/game.py::_phase_negotiation`/`_execute_negotiation_action` | B | action種別を dm/broadcast/transfer/repay/contract_propose/contract_sign/pass の7種に絞る（市場・カードトレード・報奨・匿名・倍掛けを削除）。手番ランダム・全員連続パス早期終了・最大10巡はそのまま | `test_dm_secrecy.py` ほか | 1,000相当→約400 |
| ゲームループ | dangou `engine/game.py`（2,640行） | B | 5フェイズ構成(Open/Negotiation/Commit/Settlement/Finance)は流用。脱落関連の全経路を削除。**LLM並列化は gentei の `_collect_actions`（ThreadPoolExecutor, 同時投票・同時借入に必須）を取り込む**（dangouは逐次） | `test_game_loop.py`(gentei) | 2,640→約900 |
| 財務通知 | dangou `prompt_builder._render_finance_block`/`_compute_finance_forecast` | B | 2種債務・残り枠・利息見込み・今R期限の義務・自己順位の6項目へ | `test_prompt_finance.py` | 300→約200 |
| 順位通知 | dangou `engine/player.assets_ranking`（`AssetRank(rank, tied, n_alive)`）+ `_render_rank_self_notice` + `RANK_NOTIFIED` | A | 同額同順位の `tied` が仕様 §7.3 と一致。R3/R6/R9 の全体公開だけ追加 | `test_rank_notice.py` | 60 |
| 脱落 | dangou `engine/elimination.py` | — | **使わない**（少数決に脱落なし） | — | 0 |
| Bot土台 | gentei `bots/base.py`+`protocol.py`+`__init__.py`(BOT_REGISTRY) | B | `_try_exit` 削除、`_try_full_repay` は残す。Bot本体6種（ランダム/常にYES/票を割る/前回少数派追従/契約破り/票を買う）は新規 | `test_bots.py`(dangou) | 124+新規 約300 |
| 実行スクリプト(Bot) | gentei `scripts/simulate.py`（ロスター`Name:数`展開・multiprocessing・CSV） | B | 12人固定・少数決の集計項目（6対6率・少数派人数分布・最終資産分布・取りはぐれ額・王様作り）へ | `test_simulate.py`(dangou) | 116→約200 |
| 実行スクリプト(LLM) | gentei `scripts/llm_trial.py` | B | 引数 `--roster --seed --game-id --per-player-cap-usd --game-cap-usd --parallel --no-preflight` はそのまま流用。`--turns` → `--rounds`、`--questions` 追加。veteran系は落とす。seat_map.json 出力は流用 | `test_preflight.py` | 425→約350 |
| dry_run | gentei `scripts/dry_run.py` | B | 12人版へ | — | 86 |
| デタッチ起動 | gentei `scripts/run_trial.sh`/`check_trial.sh`（`setsid nohup`） | A | なし。セッションタイムアウト回避に必須 | — | 小 |
| DevRelayスモーク | gentei `tools/devrelay_smoke.py`(205) `tools/fake_devrelay.py`(147, dangouと0行差) | A | なし | `test_devrelay_http.py` | 352 |
| ビューア | 各プロジェクトの `viewer/` | B | 後回し（別サイクル）。`server.py` の public/god 2段認証とホワイトリスト方式 `PUBLIC_EVENT_DATA_KEYS` は必ず踏襲。`log_parser.py` は少数決イベント向けに書き直し。感情画像42枚と `style.css` は流用可 | `test_viewer*.py` 約10本 | 1,300〜2,300 |
| 匿名化 | — | — | プロンプトにモデル名を出さない方針＋`{game_id}_seat_map.json` で席→モデルを別ファイル保存（gentei `llm_trial.py:349-362`）。流用A | — | 小 |

**ログ形式**: `logs/llm/{game_id}_events.jsonl` / `{game_id}_llm_calls.jsonl` / `{game_id}_seat_map.json` の3点。`EventLogger.log(event_type, round_num, phase, data, step)` と `LLMLogger.log_call(...)` の形をそのまま使う。イベント種別は dangou の29種から市場/倍掛け/脱落系を削り、`VOTE_COMMITTED` `MINORITY_RESOLVED` `CARRYOVER` `TYPE_B_VIOLATION` `PAYMENT_SHORTFALL` 等を追加する。

**合計の目安**: 無改変コピー約2,900行（llm基盤+tools+events+rng+costing+ranking）、改造コピー約3,500行、新規約1,400行。

## 2. gentei-janken は dangou-card からどう流用したか

- **コピー（依存ではない）**。`doc/devlog/2026-09-25_231347.md`（サイクル1.0）に明記: `llm/` の約2,400行を「engine非依存のためほぼ逐語コピー」、`engine/` は仕様書に沿って新規実装、`viewer/server.py` は流用、`log_parser.py` は新規。パッケージ依存・submodule・シンボリックリンクは一切使っていない。実測差分も裏付け: `costing.py` 0行差 / `fake_devrelay.py` 0行差 / `constants.py` 3行差 / `models.py` 7行差 / `devrelay_http.py` 14行差 / `adapters.py` 27行差 ↔ ゲーム依存の `contracts.py` 1,554行差 / `response_parser.py` 1,366行差 / `llm_agent.py` 1,081行差。
- **うまくいった点**: ① engine非依存のLLM層を丸ごと移せた（初日に90テストPASS＋20人×120ターン完走の「歩く骨格」）。② `rules/project.md` に設計規律（config単一ソース・CoT非漏洩・DM秘匿・ビューア白リスト・`usage_cost()`経由・デタッチ起動）を dangou の教訓ごと持ち込んだ。③ `.gitignore` は dangou 版を基に `.DS_Store`/`node_modules` を足しただけ。
- **困った点**（少数決で先に手を打つべき点）: ① **DevRelay の `DEFAULT_TARGET_PROJECT_ID` が dangou の cuid のままで、放置すると他プロジェクト宛にリクエストが飛ぶ実害**（サイクル2.7で発覚。移植直後にcuid差替とテストを入れる）。② dangou の SDK依存が `httpx` ではなく `httpx2` を引き実行時 `ModuleNotFoundError`（`httpx>=0.28.1` を直接依存に明記）。③ **CSSだけ付いてきて機能が付いてこない部分移植**（`final-reflection` が孤児CSSとして残存、サイクル2.30で発覚）→ 移植は「機能単位」で、CSS/定数だけ先行させない。④ 同型バグの片側だけ直す事故（`build_final_reflection_prompt()` の `"json"` 語欠落で openai_compat 系が400、サイクル2.33）→ プロンプト生成関数は全数で `"json"` 語を検査する。⑤ `rules/devrelay.md` は DevRelay scaffold 配布版（3行）を採用し、dangou の旧v6版（174行）で上書きしないこと。

## 3. 部品ごとの流用元の優劣

- **gentei-janken が良い**: LLM基盤一式（adapters/models/costing/game_cost_budget/llm_logger/constants/providers＝最新・バグ修正済み）、`engine/events.py`（逐次追記+Lock）、`engine/rng.py`、`bots/base.py`（簡潔）、`scripts/simulate.py`・`llm_trial.py`・`run_trial.sh`、`engine/game.py` の**並列LLM収集**（`ThreadPoolExecutor`、dangouには無い）、`llm/llm_agent.py`、`rules/project.md`、`pyproject.toml`（`package=false`）、`.gitignore`。
- **dangou-card が良い**: 契約の型A/B/C（gentei は型A/B/C/Dだがジャンケン固有で、**少数決の型定義は dangou とほぼ同形**）、`ConditionType`、`engine/config.py`（12人12R・借入120万〜1000万・利息1.5%・参加費10万・交渉10巡が**そのまま一致**）、`llm/prompt_builder.py` の財務/義務/順位/AUTO COMMIT/契約テンプレ各ブロック、`llm/response_parser.py` の多段サルベージ、`engine/autocommit.py`、`engine/player.assets_ranking`、`engine/negotiation.py` の `choose_loan` 付きPlayerAgent、感情画像42枚と `viewer/static/`。
- **どちらも使えない（新規）**: 少数決判定・配当・持ち越し、2種債務＋上限1000万、`contract_seq` 順の部分払い、質問生成。

## 4. ビューア（別プロジェクト）

`/home/devrelay/testflight/dangou-card-viewer` は**読める**が、中身は `CLAUDE.md`・`doc/changelog.md`(12バイト)・`placeholder/index.html`(1,282バイト)・`rules/` のみで、ビューアの実装コードは入っていない（配信用の置き場）。実装は各プロジェクトの `viewer/` にある。深追いしない。

## 5. 仕様書を読んで気づいた矛盾・あいまいな点（判断はしない／全16件）

1. §7.1 手順5で支払上限を「契約受取前」に固定するのに、手順7の借金確定は「受取と支払いを相殺した後」。受入テスト13（残り枠十分→借金0・現金50万）と14（残り枠0→支払0・現金200万）は整合するが、実装順の指定が必要。
2. §4.3 の「120万は1〜5で割り切れるため通常は発生しない」は、持ち越しがある場合（例: 多数派70万＋持ち越し120万=190万 ÷ 少数派3人）に成り立たない。切り捨て・余り没収は必要。
3. 型C の `round_num`（義務の期限）と、条件が判定されるラウンドが同一かどうかが未明記（§9.3の例は同一ラウンドのみ）。別ラウンドを指定できるのか。
4. §5.1 の「直近10問」履歴ファイルのパス・形式・ローテーション方法が未定義。
5. §4.4 の「P07: AUTO COMMIT」の `P07` が公示コードなのかプレイヤーIDなのか不明（dangou-card側に `P0n:` 形式の公示コード体系は見つからなかった）。
6. §3.2 で transfer は「手持ちの現金まで」。dangou-card は `free_cash = max(0, cash − debt)` 基準なので**基準が変わる**。交渉中のtransferが即時決済（§1）である点と合わせ、現金基準で確定させる必要がある。
7. 契約の取消（dangou の `request_cancel`/`can_cancel_contract`）が仕様書に一切出てこない。入れるのか落とすのか。
8. memory（ラウンド間引き継ぎ）・reflection・FINAL_REFLECTION・POST_GAME_REFLECTION の扱いが未記載（dangou/gentei にはある）。
9. 「入れない機能」に匿名通信・公開報奨があるので `anonymous_broadcast`・`bounty` は削除で確定だが、`§9.2` のaction一覧に `vote_commit` 以外のCommitフェイズ行動（例: 無効時の再試行回数）が未記載。
10. Commit で参加費を徴収（§7.1-3）した結果の借金増加が、同ラウンド手順5の「残り枠」に反映されるのか（順序上は反映されるが明記なし）。
11. §3.4 の「参加費だけは上限を無視して必ず貸す」が、借金合計が上限を超えた状態での残り枠=0 と併記されており、参加費による超過分がその後の利息計算に乗るかどうかは §3.3 からの推論になる。
12. §7.1 手順3の型B監査は手順5の上限固定より前。違約金は手順6で `contract_seq` 順に執行される（受入21と整合）が、「違約金は元になった契約の番号で並べる」際に同一契約内の型A義務との相対順序が未明記。
13. §6.3「同じラウンドにYES指定とNO指定の両方を負える」＝必ず片方違反。§4.4 の自動代行で「指定が矛盾」のとき乱数で選ぶのと合わせ、違約金200万が確定する経路になる（意図通りか要確認）。
14. 順位の公開タイミングが §7.3 で「R3・R6・R9 の Finance 終了後」、§7-5 で「R3・R6・R9 はその後に公開」。R12 は「終了後に最終順位と最終資産額」。R12 で R3/R6/R9 型の公開もするのかは未記載。
15. プレイヤー数12に対しモデルロスターは `uso8000000_model_roster_v1_0.md` の12モデル＋DR席。12人固定でロスター指定をどう受けるか（dangou/gentei は20人/18人運用）。
16. §11.2 の未決2点（エンジンをどこまで流用するか＝本調査、§13 ルール要約の文面確定）。

## 6. 実装の段取り案

| サイクル | 内容 | 規模感 |
|---|---|---|
| 0.2 | 基盤: `git init`+remote、`pyproject.toml`（`package=false`・`httpx` 直接依存）、`rules/project.md`（gentei版を少数決向けに）、`CLAUDE.md`、`llm/` 基盤の逐語コピー（adapters/models/costing/constants/llm_logger/game_cost_budget/providers 約1,850行）、`tools/` 352行、`engine/events.py`+`rng.py`、`tests/test_devrelay_http.py`。**DevRelay cuid を shousuu-ketsu のものに差替＋検証テスト**（gentei の実害を再発させない） | 小〜中（コピー約2,400行＋新規約200行、テスト約35件） |
| 1.0 | 歩く骨格: `engine/models.py`（PlayerState 2種債務・Vote・Action 8種）、`config.py`、`player.py`、`negotiation.py`、少数決判定・配当・持ち越し、`finance.py`（2利率複利）、`game.py` の12R×5フェイズ、Bot 2種、`dry_run.py`。受入テスト §12.3 の #1〜#7,#10,#11 | 大（新規約1,500行、テスト約60件） |
| 1.1 | 契約: 型A/B/C + `contract_seq` 付番 + §7.1 の8手順決済（部分払い・取りはぐれ・借金確定）。受入 #8,#9,#12〜#14,#16,#19〜#22 | 中〜大（コピー改造約700行＋新規約500行、テスト約50件） |
| 1.2 | 質問生成: `question_model` 1回呼び・機械検査・予備リスト・履歴ファイル・`--questions`。受入 #17,#25 | 小〜中（新規約250行、テスト約15件） |
| 1.3 | プロンプト・パーサ: `prompt_builder.py`（dangouから部分移植）、`response_parser.py`、`phase2_schema.py`、`llm_agent.py`。§13 ルール要約を正本化。受入 #23,#24 | 大（改造コピー約2,300行、テスト約40件） |
| 1.4 | Bot 6種 + `simulate.py` で §12.1 の8項目を実測（6対6率・持ち越し最大・少数派人数分布・最終資産分布・借入額と順位・型B違反件数・取りはぐれ・王様作り） | 中（新規約500行、テスト約20件） |
| 1.5 | LLM通電: `llm_trial.py`・`run_trial.sh`・preflight・席キャップ・DR席・`seat_map.json`。小規模実戦1本 | 中（改造コピー約450行、テスト約20件） |
| 2.x | ビューア（public/god 2段認証・ホワイトリスト方式・感情画像流用）、本戦、ブログ | 大 |

## 7. 流用元のコミット

- dangou-card: `5b8b16de82e7f325e40169e0dc2bb580dbe7a30e`（2026-10-05 12:33:50 +0900 / サイクル10.23）
- gentei-janken: `2cb1e14595875c038e7a415aa175f913bff4d6dc`（2026-09-30 07:57:23 +0900 / サイクル2.36）
