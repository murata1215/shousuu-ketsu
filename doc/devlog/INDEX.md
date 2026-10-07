# devlog INDEX

devlog本文は `doc/devlog/YYYY-MM-DD_HHMMSS.md` に1サイクル1ファイルで保存する。
このINDEXには1行だけ追記し、`doc/devlog/` の一括catは禁止。

## 索引

- 2026-10-06_073837 | サイクル0.1 | 流用調査 | dangou-card/gentei-jankenの部品表（約35項目）・流用方式・優劣・仕様書あいまい点16件・段取り案をdoc/analysis/reuse_investigation.mdにまとめた（実装なし、読み取りのみ）
- 2026-10-06_170209 | サイクル0.2 | 基盤づくり | git init+remote・uv環境・gentei-jankenからAI呼び出し層15ファイル約2,400行をコピー（DevRelay宛先cuidは既定値廃止し環境変数必須化）、テスト41件全PASS、1コミットでmainへpush
- 2026-10-07_041047 | サイクル1.0 | 動く骨格 | engine/13+bots/4+scripts/1+tests/8ファイル計2,673行を新規作成し、契約なしで12ラウンドの投票・配当・持ち越し・2種債務を完走。テスト76件全PASS（§12.3の#1,2,3,4,5,6,7,10,15,16,23,24含む）、無作為100試合で帳尻/現金非負/借金上限を確認、利息は整数切り上げ除算で実測値と一致
- 2026-10-07_053239 | サイクル1.1 | 契約 | engine/contracts.py新規329行＋既存6ファイル改修（dangou-cardのcontracts.py/modelsをB分類で流用、contract_idはシード乱数8桁・連番にしない）で型A/B/C・成立順contract_seq・§7.1手順3〜8を実装。テスト115件全PASS（既存76件維持＋新規39件、§12.3受け入れ#8,9,11,12,13,14,19,20,21,22含む）、契約入り無作為100試合で帳尻/支払上限/部分払い後0円/同シード一致を確認、dangou-card/gentei-jankenは差分増減なし
- 2026-10-07_074930 | サイクル1.2 | Bot検証 | 前セッション途中終了（連打検知）分を引き継ぎ、sim/store.py新規130行＋scripts/simulate.py改修で保存・再開可能なシミュレーションCLIに変更。テスト169件全PASS（既存162件＋新規7件）、S1〜S6（S3はk=1/3/6）各1,000試合をdata/sim/（gitの管理外）に保存し分割実行の数字一致をバイト単位で確認、doc/analysis/bot_simulation_report.md（172行）を保存済みJSONのみから作成。dangou-card/gentei-jankenは差分増減なし
- 2026-10-07_130436 | サイクル1.3 | 開始前の借金を返済不可に | 仕様書v0.3（§3.5）に合わせてengine/player.py::repay()を開始後の借金のみに限定し、LoanMaxRepayBotをLoanMidHoldBot（500万）に置換、S5を120万/500万/1000万の4人ずつに組み替えた。テスト174件全PASS（169件→174件）。Bot検証8条件を1本ずつ表で回し直しdata/sim_v0_3/に保存、doc/analysis/bot_simulation_report_v0_3.mdを新規作成（S5以外の7条件は前回と全数値一致、S5のみプラス人数平均2.64→1.56人等が変化）。dangou-card差分なし、gentei-janken増分1件は本作業と無関係（他セッション起因、書き込みなし）
- 2026-10-07_190723 | サイクル1.4 | 質問生成とプロンプト | llm/questions.py・prompt_builder.py・response_parser.py・llm_agent.py（計1,691行、新規/dangou-card・gentei-janken改造コピー）でAI席の部品一式を作成。engine/game.pyにon_question_publishedフック、engine/config.pyにquestion_model追加。環境・利用者に触れない一文はDevRelay席のみprovider側1回付与に寄せ二重化を防止。テスト267件全PASS（174件→267件）。実AI確認4回・実測$0.00126（質問生成DR_HAIKU$0、交渉疎通L3$0.00126・DR_HAIKU$0、L7は404失敗）。dangou-card差分なし、gentei-janken増分は本作業と無関係（他セッション起因、書き込みなし）
- 2026-10-07_202511 | サイクル2.0 | AIをつなぐ | scripts/llm_trial.py・summarize_trial.py新規（計729行）とengine/game.pyの並列化・試合後振り返り配線で12席AI試合を1本で回せるようにした。テスト278件全PASS（267件→278件）。実AI確認3本＋要約1本・実測41回・約0.0085ドル（12席疎通全OK、質問生成12問、4席[L3,L6,DR_HAIKU,DR_LUNA]1R交渉2巡の通し完走）。dangou-card差分なし、gentei-janken増分は他セッション起因（read-onlyのみ）
- 2026-10-08_014458 | サイクル2.1 | 試走の修正と本戦の準備 | 打ち切り試合での試合後振り返り省略・途中経過の1行ごとflush・振り返り末尾「}」取りこぼし修正・要約コマンド拡張（席とモデル・借入額一覧、--transcript/--roundでの会話の書き出し）、加えてイベント自体に発言本文・契約内容を追加（§8の観戦者区分どおり、プレイヤー可視状態は未変更）。テスト296件全PASS（278件→296件）。既存記録r1_12p_2001で新機能を実行確認し、P08のR1交渉（表ではYES確定と言いつつP10へNOを勧め自分はYESの少数派で+14万）とP09のP02裏切り暴露（元DMで裏付け）を事実として抜き出した。dangou-card差分なし、gentei-janken増分は着手前後でバイト単位一致（他セッション起因）
