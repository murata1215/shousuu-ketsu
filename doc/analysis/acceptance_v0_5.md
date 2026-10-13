# 受け入れテスト対応表（v0.5 §12.3、サイクル4.0・4.1・4.2・4.2c・4.2d・5.0）

仕様書 `doc/uso8000000_shousuu_ketsu_spec_v0_5.md` §12.3 の78件すべてに
対応するpytest関数の一覧。命名は既存コードベース（v0.3）の慣例
（`test_acceptance_{番号}_{内容}`）に揃えた。#47・#48は出題AI・履歴ファイル
（`llm/questions.py`）の対応がサイクル4.2bで完了し、合格になった（下記注2参照）。
#51〜#54はサイクル4.1で追加された（v0.4.1 §11.6 #8。§8.1「公示をプレイヤーに
渡す形」の確認）。#55・#56はサイクル4.2で追加された（v0.4.2 §11.7 #4。
§9.4「ラウンドの中は投票をまたいで会話を引き継ぐ」の確認。engine/game.pyの
修正は実装済み）。#57も同#4で追加され実装済み（§7.5「不成立の理由を
日本語の文で本人にだけ渡す」の確認。engine/actions.py::RejectReason・
engine/contracts.py::TermRejectReason・llm/reasons.py::reject_reason_jaで
対応）。#58〜#66はサイクル4.2cで追加された（v0.4.3 §11.8。本人・当事者だけに
渡すお金の出入りの記録・義務ごとの状態・契約の表示範囲・文面の15,000字上限・
試合後振り返り・署名済み表示・メモ字数上限の確認。対応は
`tests/test_acceptance_58_to_68.py`）。v0.4.3はv0.4.2からルールの変更はなく、
サイクル4.2の結果を反映した書き直し（#55・#56の文言更新、#58〜#66の追加）
で、#1〜#57の実装はそのまま（CLAUDE.mdの「v0.4.2はv0.4.1からルールの変更は
ない」と同じ位置づけ）。

#66はサイクル4.2dでv0.4.4に合わせて書き直した（求める字数1,000字と切り捨てる
字数1,500字を分けたため、場面を1,200字/1,600字のメモに変更）。#67（署名待ちの
提案を2つの見出しに分ける）・#68（契約IDの「契約」除去）はサイクル4.2dで新規
追加（v0.4.4 §11.9 #3・#4。対応は`tests/test_acceptance_58_to_68.py`）。

#69〜#78はサイクル5.0で追加された（v0.5 §11.10。型Cに4つ目の条件「山分け」（`pool_share`）を足したことの確認。対応は`tests/test_acceptance_69_to_78.py`）。v0.5のルールの変更は山分けの新設と、ルール要約の書き足し（§13.1に「対象には今より後の投票・ラウンドも指定できる」の1行。既存のルール§6.1の確認であり実装の変更は不要）の2点だけで、#1〜#68の実装はそのまま。

| # | 場面（仕様書の表現） | pytest関数 | 状態 |
| --- | --- | --- | --- |
| 1 | R1V1が7対5 | `tests/test_vote_round.py::test_acceptance_01_decisive_7_against_5_no_money_moves` | 合格 |
| 2 | R1V1が11対1 | `tests/test_vote_round.py::test_acceptance_02_decisive_11_against_1_ends_round` | 合格 |
| 3 | R1V1が10対2 | `tests/test_vote_round.py::test_acceptance_03_decisive_10_against_2_ends_round` | 合格 |
| 4 | 5人が残った次の投票が3対2 | `tests/test_vote_round.py::test_acceptance_04_five_remaining_3_against_2_ends_round` | 合格 |
| 5 | 5人が残った次の投票が4対1 | `tests/test_vote_round.py::test_acceptance_05_five_remaining_4_against_1_ends_round` | 合格 |
| 6 | R1V1が6対6 | `tests/test_vote_round.py::test_acceptance_06_tie_6_against_6_is_retry` | 合格 |
| 7 | R1V1が12対0 | `tests/test_vote_round.py::test_acceptance_07_unanimous_12_against_0_is_retry` | 合格 |
| 8 | 4人が残った投票が2対2 | `tests/test_vote_round.py::test_acceptance_08_four_remaining_tie_only_remaining_pay` | 合格 |
| 9 | R1のV1〜V3がすべて6対6 | `tests/test_vote_round.py::test_acceptance_09_three_consecutive_ties_abort` | 合格 |
| 10 | やり直し2回の後に決着し、その後やり直しが2回 | `tests/test_vote_round.py::test_acceptance_10_consecutive_count_resets_on_decisive` | 合格 |
| 11 | R4が打ち切り | `tests/test_vote_round.py::test_acceptance_11_r4_abort_destroys_pot` | 合格 |
| 12 | V1が7対5、V2が5対0、V3が3対2 | `tests/test_vote_round.py::test_acceptance_12_payout_after_retry_and_decisive` | 合格 |
| 13 | やり直し2回→決着→やり直し2回の後のV6 | `tests/test_vote_round.py::test_acceptance_13_vote6_decides_round_without_vote7` | 合格 |
| 14 | R2の開始 | `tests/test_game_loop.py::test_acceptance_14_round2_start_resets_all_12_and_collects_entry_fee` | 合格 |
| 15 | 退場者がいる投票 | `tests/test_game_loop.py::test_acceptance_15_eliminated_players_skip_commit_but_can_negotiate` | 合格 |
| 16 | 現金20万でR2を開始 | `tests/test_player_debt.py::test_acceptance_16_cash_zero_entry_fee_becomes_post_debt` | 合格 |
| 17 | 借金残高1000万・現金0でラウンドを開始し、V1がやり直し | `tests/test_player_debt.py::test_acceptance_17_entry_fee_and_extension_fee_exempt_from_cap` | 合格 |
| 18 | 開始前の借金120万だけを持ち、4ラウンド経過 | `tests/test_finance_interest.py::test_acceptance_18_pre_interest_120man_over_4_rounds` | 合格 |
| 19 | R1で開始後の借金100万を負い、返さずに4ラウンド経過 | `tests/test_finance_interest.py::test_acceptance_19_post_interest_100man_over_4_rounds` | 合格 |
| 20 | 投票が6回あったラウンド | `tests/test_finance_interest.py::test_acceptance_20_interest_posted_once_per_round_regardless_of_vote_count` | 合格 |
| 21 | 開始前の借金120万と開始後の借金50万がある状態で60万を返済 | `tests/test_player_debt.py::test_acceptance_21_repay_only_post_debt` | 合格 |
| 22 | 開始後の借金が0の状態で返済を指定 | `tests/test_player_debt.py::test_acceptance_22_repay_without_post_debt_is_rejected` | 合格 |
| 23 | 手持ち20万で50万を送金 | `tests/test_player_debt.py::test_acceptance_23_transfer_exceeding_cash_is_rejected` | 合格 |
| 24 | 型B「R1V1はYES」でNOに投票。精算時の現金30万 | `tests/test_settlement_v0_4.py::test_acceptance_24_type_b_violation_partial_borrow` | 合格 |
| 25 | 型B「R1V2はYES」。義務者はV1で退場 | `tests/test_settlement_v0_4.py::test_acceptance_25_type_b_expires_if_eliminated_before_target_vote` | 合格 |
| 26 | 型B「R1V3はNO」。ラウンドはV2で終了 | `tests/test_settlement_v0_4.py::test_acceptance_26_type_b_expires_if_round_ends_before_target_vote` | 合格 |
| 27 | 型B「R1V1はYES」でNOに投票。V1は6対6 | `tests/test_settlement_v0_4.py::test_acceptance_27_type_b_violation_applies_on_retry_vote` | 合格 |
| 28 | 型C `minority_side`「R1V1の少数派がYES」。V1は6対6 | `tests/test_settlement_v0_4.py::test_acceptance_28_type_c_minority_side_unmet_on_tie` | 合格 |
| 29 | 型C `in_minority`「R1V2でP03が少数派に入る」。P03はV1で退場 | `tests/test_settlement_v0_4.py::test_acceptance_29_type_c_in_minority_unmet_if_eliminated` | 合格 |
| 30 | 型C `in_minority`「R1V1でP03が少数派に入る」。V1は7対5でP03は5人の側 | `tests/test_settlement_v0_4.py::test_acceptance_30_type_c_in_minority_met` | 合格 |
| 31 | 型C `wins_round` 固定額。P01が1人で勝ち残る | `tests/test_settlement_v0_4.py::test_acceptance_31_type_c_wins_round_fixed_amount` | 合格 |
| 32 | 型C `wins_round` 割合25%。義務者P01が2人勝ちで600万を受け取る | `tests/test_settlement_v0_4.py::test_acceptance_32_type_c_wins_round_share_percent` | 合格 |
| 33 | P01が割合50%の契約を3本持ち、1人勝ちで1,200万を受け取る | `tests/test_settlement_v0_4.py::test_acceptance_33_type_c_share_percent_stacking` | 合格 |
| 34 | 33と同じ条件で、残り枠が300万 | `tests/test_settlement_v0_4.py::test_acceptance_34_type_c_share_percent_stacking_with_shortfall` | 合格 |
| 35 | 打ち切りのラウンドに、`wins_round`の契約と型Aがある | `tests/test_settlement_v0_4.py::test_acceptance_35_aborted_round_skips_wins_round_but_pays_type_a` | 合格 |
| 36 | 割合指定を`wins_round`以外で使う。または対象者が義務者でない | `tests/test_contracts.py::test_acceptance_36_share_percent_rejected_outside_wins_round_or_other_target` | 合格 |
| 37 | 型A「R2に200万」 | `tests/test_settlement_v0_4.py::test_acceptance_37_type_a_paid_at_round_end_not_mid_round` | 合格 |
| 38 | 同じ精算で200万受け取り・150万支払い。現金0、残り枠は十分 | `tests/test_settlement_v0_4.py::test_acceptance_38_no_debt_when_credit_sufficient` | 合格 |
| 39 | 同じ精算で200万受け取り・150万支払い。現金0、残り枠0 | `tests/test_settlement_v0_4.py::test_acceptance_39_receivable_not_usable_as_funding_source` | 合格 |
| 40 | 支払える上限400万。成立順1でP02へ300万、成立順5でP08へ300万 | `tests/test_settlement_v0_4.py::test_acceptance_40_payable_limit_split_by_contract_seq` | 合格 |
| 41 | 提案はC05が先、全員の署名がそろったのはC01が先 | `tests/test_contracts.py::test_acceptance_41_contract_seq_assigned_in_establishment_order_not_proposal_order` | 合格 |
| 42 | 支払える上限100万。成立順1の型B違約金100万と、成立順7の型C（同じ投票が対象）100万が同じ精算 | `tests/test_settlement_v0_4.py::test_acceptance_42_partial_type_b_penalty_before_type_c_same_vote` | 合格 |
| 43 | 契約が成立した巡の終わり | `tests/test_secrecy_contracts.py::test_acceptance_43_non_party_state_has_no_contract_trace`（公示の本数のみであることは`tests/test_secrecy_contracts.py::test_turn_disclosure_contains_only_count_no_names`でも確認） | 合格 |
| 44 | 署名がそろわない提案 | `tests/test_contracts.py::test_acceptance_44_unsigned_proposal_expires_at_vote_end` | 合格 |
| 45 | 交渉の巡 | `tests/test_game_loop.py::test_acceptance_45_negotiation_turn_limits_by_vote_kind` | 合格 |
| 46 | 時間切れ。その投票に型B「YES」指定あり | `tests/test_autocommit_type_b.py::test_acceptance_46_timeout_with_type_b_yes_instruction_auto_commits_yes` | 合格 |
| 47 | 出題AIの呼び出し失敗 | `tests/test_questions.py::test_acceptance_47_adapter_failure_falls_back_to_pool_avoiding_recent_30` | 合格 |
| 48 | 試合で使った質問が9問 | `tests/test_questions.py::test_acceptance_48_only_used_9_questions_appended_to_history`（エンジン側の対応する範囲は`tests/test_game_loop.py::test_on_question_published_called_only_for_votes_actually_used`で確認済み） | 合格 |
| 49 | R2のFinance終了後 | `tests/test_rank.py::test_acceptance_49_r2_public_ranks_names_only_same_amount_same_rank` | 合格 |
| 50 | 借入額の選択中 | `tests/test_game_loop.py::test_acceptance_50_choose_loan_cannot_see_others` | 合格 |
| 51 | 契約が成立した巡の、次の手番 | `tests/test_public_disclosure.py::test_acceptance_51_turn_contract_count_visible_from_next_turn_only` | 合格 |
| 52 | R1V1で型B違反・払いきれなかった者・自動代行が出て、ラウンドがV2へ続く | `tests/test_public_disclosure.py::test_acceptance_52_vote_settlement_disclosures_visible_from_next_vote_open` | 合格 |
| 53 | R1V3の交渉中。R2の交渉中 | `tests/test_public_disclosure.py::test_acceptance_53_vote_and_round_history_accumulate_across_the_game` | 合格 |
| 54 | 形式に合わない提案をした、次の手番 | `tests/test_public_disclosure.py::test_acceptance_54_rejection_reason_only_to_proposer` | 合格 |
| 55 | R1V2の交渉中 | `tests/test_public_disclosure.py::test_acceptance_55_round_conversation_carries_across_votes` | 合格 |
| 56 | R1V1が決着してV2へ続くとき。R1の最後の投票の後 | `tests/test_public_disclosure.py::test_acceptance_56_reflection_sees_whole_round_and_next_round_starts_empty` | 合格 |
| 57 | 手持ちを超える送金をした、次の手番 | `tests/test_public_disclosure.py::test_acceptance_57_rejection_reason_is_japanese_sentence_to_actor_only` | 合格 |
| 58 | P01がP02へ30万を送金した、次の手番 | `tests/test_acceptance_58_to_68.py::test_acceptance_58_transfer_recorded_only_for_sender_and_receiver` | 合格 |
| 59 | R1V1で型B違反があり、ラウンドがV2へ続く | `tests/test_acceptance_58_to_68.py::test_acceptance_59_type_b_penalty_visible_to_parties_only` | 合格 |
| 60 | R1の終わりに、割合契約（25%）の支払いと、払いきれなかった型Aがある | `tests/test_acceptance_58_to_68.py::test_acceptance_60_round_settlement_payments_visible_with_payout_and_interest` | 合格 |
| 61 | R1V1を対象にした型Cが支払われた後の、R1V2 | `tests/test_acceptance_58_to_68.py::test_acceptance_61_type_c_payment_moves_obligation_to_paid_in_my_contract_status` | 合格 |
| 62 | 試合後の振り返り | `tests/test_acceptance_58_to_68.py::test_acceptance_62_post_game_reflection_carries_results_ranks_and_cash_flow` | 合格 |
| 63 | P01が提案して署名済みの提案が、P02の署名待ちで残っている手番 | `tests/test_acceptance_58_to_68.py::test_acceptance_63_signed_proposal_shows_signed_status_not_sign_method` | 合格 |
| 64 | 会話が積み上がり、省かなければ文面が15,000字を超える手番 | `tests/test_acceptance_58_to_68.py::test_acceptance_64_old_messages_omitted_when_body_exceeds_limit` | 合格 |
| 65 | R1ですべての義務が済んだ契約と、R2を対象にした型Aが残っている契約がある、R2V1 | `tests/test_acceptance_58_to_68.py::test_acceptance_65_fully_settled_contract_hidden_pending_contract_shown` | 合格 |
| 66 | 振り返りで、1,200字のメモを返した席と、1,600字のメモを返した席がある。別の席が、交渉の応答にメモを入れて返した | `tests/test_acceptance_58_to_68.py::test_acceptance_66_memory_truncated_at_1500_and_not_updated_by_negotiate_response` | 合格 |
| 67 | P01が提案して署名済みの提案と、P03が提案してP01の署名を待っている提案が、同時にある手番 | `tests/test_acceptance_58_to_68.py::test_acceptance_67_pending_proposals_split_into_two_headings` | 合格 |
| 68 | 署名の行動で、契約IDの前に「契約」の字を付けて送った | `tests/test_acceptance_58_to_68.py::test_acceptance_68_contract_sign_with_contract_kanji_prefix_still_succeeds`（存在しないIDの場合は`test_acceptance_68_nonexistent_contract_id_rejected_without_doubled_kanji`） | 合格 |
| 69 | 4人（P03・P05・P08・P09）がX=60の山分けを結んでいる。P03が1人で勝ち残り、2,520万を受け取る | `tests/test_acceptance_69_to_78.py::test_acceptance_69_pool_share_single_winner` | 合格 |
| 70 | 4人組（X=60）のうちP01とP02が2人で勝ち残り、660万ずつ受け取る | `tests/test_acceptance_69_to_78.py::test_acceptance_70_pool_share_two_mutual_winners` | 合格 |
| 71 | 山分けの当事者P05が、そのラウンドで退場している。ほかの当事者が山を受け取る | `tests/test_acceptance_69_to_78.py::test_acceptance_71_eliminated_party_still_receives` | 合格 |
| 72 | 山分けが成立したラウンドが打ち切りになる | `tests/test_acceptance_69_to_78.py::test_acceptance_72_aborted_round_no_pool_share_payment` | 合格 |
| 73 | R2に成立した山分けの当事者が、R1で山を受け取っていた | `tests/test_acceptance_69_to_78.py::test_acceptance_73_pool_share_established_at_r2_r1_receipt_not_subject` | 合格 |
| 74 | P05が2つの山分け（どちらも4人・X=60）に入り、1人勝ちで1,200万を受け取る。ほかの現金0、残り枠880万 | `tests/test_acceptance_69_to_78.py::test_acceptance_74_pool_share_stacking_two_contracts_causes_debt` | 合格 |
| 75 | P01が、山分けでP02から分け前を受け取る。P01は別の山分けにも入っている | `tests/test_acceptance_69_to_78.py::test_acceptance_75_received_pool_share_not_re_split_or_used_as_funding` | 合格 |
| 76 | 受取額1,000,000円、X=50、当事者4人 | `tests/test_acceptance_69_to_78.py::test_acceptance_76_pool_share_floor_division` | 合格 |
| 77 | 1つの契約にpool_shareが2本ある。またはshare_percentが0か101、またはamountを指定した | `tests/test_acceptance_69_to_78.py::test_acceptance_77_pool_share_duplicated_rejected`（amount: `test_acceptance_77_pool_share_amount_rejected`／0: `test_acceptance_77_pool_share_share_percent_zero_rejected`／101: `test_acceptance_77_pool_share_share_percent_101_rejected`） | 合格 |
| 78 | pool_shareの項にobligor・counterparty・round_numが書かれている | `tests/test_acceptance_69_to_78.py::test_acceptance_78_pool_share_ignores_obligor_counterparty_round_num`（提案の形式検証だけでなく、AIの応答の読み取り→提案→署名→成立→ラウンドの精算→本人に届く記録まで試合を通して確かめる1本を追加: `test_acceptance_78_full_game_wiring_with_all_stray_fields_in_pool_share_proposal`。サイクル5.0a、注8参照） | 合格 |

## 集計

- 合格: 78件（#1〜#78）。skipは0件
- 1件も省いていない（#1〜#78がすべて表に存在する）

## 注1: #42（v0.4.1での仕様書修正の反映）

v0.4（サイクル4.0時点）の仕様書の#42は原文「成立順1の型B違約金100万と、
成立順7の**型A**100万が同じ精算」だったが、v0.4は精算が「投票の精算」
（§7.3、型B違約金はここ）と「ラウンドの精算」（§7.4、型Aはここ）の2段に
分かれ、支払える上限（手順5/手順4）も精算ごとに別々に固定されるため、
型Bの違約金と型Aが同じ精算に来ることは構造的にない。サイクル4.0では、
同じ投票の精算に「成立順1の型B違約金100万」と「成立順7の型C（同じ投票を
対象にしたin_minority、100万）」を置く読み替えで、「同じ決済・成立順の
優先」という#42の検証意図を保ったまま確かめていた。

v0.4.1 §11.6 #1でこの指摘が仕様書本文に反映され、#42は現在「成立順7の
**型C**（同じ投票が対象）」という原文になっている（サイクル4.0が指摘した
とおりの形）。テスト本体はサイクル4.0時点から変更しておらず、
docstringとpytest関数名だけをこの経緯に合わせて更新した
（`_before_type_a` → `_before_type_c_same_vote`）。

## 注2: #47・#48（サイクル4.2bで解消）

サイクル4.0・4.1時点では、#47（出題AIの呼び出し失敗→予備リストから直近30問
を避けて24問を選ぶ）・#48（試合で使った質問だけを履歴ファイルに追記する）は、
出題AIの呼び出し・予備質問リストの拡張・`data/question_history.jsonl`への
追記という`llm/questions.py`の責務のためskipしていた（エンジン側の最小限の
役割「24問を受け取り、投票ごとに順に1問使う」は実装・確認済みで、#48の前段
にあたる「実際に使った投票の回数だけ質問が公開される」ことは
`tests/test_game_loop.py::test_on_question_published_called_only_for_votes_actually_used`
で確認している）。

サイクル4.2bで`llm/questions.py`をv0.4の値（`QUESTION_COUNT=24`・
`HISTORY_SIZE=30`）に直し、予備質問リストをYES寄り（`FALLBACK_QUESTIONS_
YES_LEAN`）・NO寄り（`FALLBACK_QUESTIONS_NO_LEAN`、各30問以上）の2本に分けて
両方からおよそ半分ずつ補完するようにした。`append_question()`に`vote_num`を
足し（既存のv0.3形式の行は読める・消さない・書き換えない）、#47・#48を
`tests/test_questions.py`に実装して合格にした。

## 注3: #57（不成立の理由の日本語化）の実装方法

- エンジンは理由の**コード**を持ち、日本語の**文面**は`llm/`側に置く
  （ルールの識別はエンジン、言い回しは文面層、という役割分担）。
- `engine/actions.py::RejectReason`（16種）・`engine/contracts.py::
  TermRejectReason`（26種）を新設した。`ActionResult`に`reason_code`・
  `reason_params`を足し、`engine/contracts.py::validate_terms`の戻り値を
  `str | None`から`TermRejection`（NamedTuple、code・params・message_en）に
  変えた。既存の`reason`（英語の短い文）はイベントログ・観戦者向けに
  そのまま残した。
- `visible_state["my_last_action_error"]`は、これまでの英語の文字列から
  `{"code": str, "params": dict, "message_en": str}`の辞書に変わった
  （`is None`/`is not None`で判定している既存テストはそのまま通る）。
- `llm/reasons.py::reject_reason_ja(err)`が、このコードから日本語の文を
  作る。未知のコードを渡すとKeyErrorで落ちる設計にし、理由コードを足した
  のに日本語テンプレートを書き忘れた場合に機械的に検出する
  （`tests/test_reasons.py::test_every_reject_reason_has_japanese`）。

## 注4: サイクル4.2cで見つかった文面の壊れ（A-1〜A-5）

#58〜#66の実装後、実際に文面を出力して読んだところ、受け入れ番号には
出てこない文面の不具合が5件見つかった（`llm/prompt_builder.py::
_obligation_status_sentence`の「はが」「はの」「へ済み」という壊れた日本語、
メモ未作成の案内が古い、お金の出入りの各行に「いつ」が出ない、精算内の
記録順、振り返りのmax_tokens不足）。部分一致のテストだけでは検出できな
かったため（CLAUDE.md落とし穴⑭参照）、`tests/test_obligation_sentences.py`
で72通り（義務の種類6×状態6×自分/相手）を全文一致で固定し、
`doc/analysis/prompt_samples_v0_4.md`に見本4つ（8〜11）を足して実際の
文面を目で確認する運用に変えた。

## 注5: サイクル4.2d（v0.4.4対応）の実装方法

- メモの字数は、`llm/constants.py`の`MEMORY_MAX_LENGTH`（1,000字）を
  `MEMORY_REQUEST_MAX_LENGTH`（1,000字、文面でAIに求める字数）と
  `MEMORY_TRUNCATE_LENGTH`（1,500字、実際に切り捨てる字数）の2つに分けた。
  2回目の12席試走（§12.10）でメモ72件のうち18件が1,000字を超えて
  末尾を切られたため。
- 署名待ちの契約提案は、`llm/prompt_builder.py::_render_contracts_pending_block`
  が「## あなたの署名を待っている契約提案」と「## あなたは署名済みで、
  相手の署名を待っている契約提案」の2つの見出しに分けて出す。見出し名を
  引用する`llm/reasons.py`のCONTRACT_NOT_FOUND文も合わせた。
- 契約IDの前処理（先頭の「契約」の字と直後の空白を外す）は
  `llm/response_parser.py::_strip_contract_id_prefix`で行い、エンジンは
  変更していない。
- Opus席（H1）は`llm/models.py`でAPIを直接呼ぶ席に替え、M1と同じ
  `extra_params={"thinking": {"type": "disabled"}}`を追加した
  （`DR_OPUS`は変更していない。受け入れテストの対象外）。
- `doc/analysis/prompt_samples_v0_4.md`に見本12（R1V1・8巡目、2つの見出しが
  同時に出る場面）を足し、実際の文面を目で確認した。

## 注6: サイクル5.0（v0.5・山分け）の実装方法

- 中の持ち方は「精算のときに計算する」案を採った。契約の中には山分けの
  義務を1行だけ置き（`engine/models.py::Obligation`、obligor・
  counterparty・round_numをすべて持たない）、実際に誰が誰にいくら払うかは
  ラウンドの精算のたびに計算する（`engine/contracts.py::
  pool_share_obligations_in_effect`/`pool_share_fan_out_amount`、
  `engine/settlement.py::_collect_round_due_items`）。成立時に当事者の組×
  残りラウンド数の分だけ義務を展開する案は、見せる契約の中身が人数×
  ラウンド数で膨らんで読めなくなるため採らなかった。
- 山分けの義務は、ほかの型Cと違い毎ラウンド判定され続けるため、当事者に
  見せる契約の中では常に「これから（PENDING）」の表示にする
  （`engine/game.py::_obligation_status_view`）。実際に動いた額は
  お金の出入りの記録（`my_cash_flow`）と、本人向け財務通知の
  `my_pool_shares`（当事者・割合・効く期間）に出す。
- 支払い・受け取りの記録は、新しいイベント種別を作らず、既存の
  `CONTRACT_PAYMENT`に`condition_type`を1項目足して、ほかの型Cと
  見分けられるようにした（`ObligationPayment`/`ObligationOutcome`/
  `CashFlowEntry`の3モデルに追加）。
- ルール全文（`llm/prompt_builder.py::RULES_TEMPLATE`）はv0.5 §13.1と
  逐語一致に更新した（差分は5か所: 条件が3種→4種、山分けの説明7行＋
  「後の投票・ラウンドも指定できる」1行、進行の1行、書き方の例の1行、
  注3本の書き直し）。
- AIの応答の読み取り（`llm/response_parser.py`）に、山分け専用の形
  （obligor等を持たない）を通した。
- Bot確認: 山分けを結ぶBotがいない試合（シナリオV2）は変更の前後で
  挙動が変わらないこと、山分けを結ぶBot（`bots/pool_share_bot.py::
  PoolShareGroupBot`、シナリオV11）を使った試合で、実際の支払いが
  §6.4の計算（受取額×X÷100÷（当事者数−1）、1円未満切り捨て）と
  一致することを確認した（devlog参照）。

## 注7: 小さな直し（deepseek救済・公開前チェック・devlog）

- `llm/response_parser.py::parse_response`に2段階の救済を足した:
  (1) JSONが2つ並ぶ応答（最初がstrategyだけ、2つ目に行動が入っている）
  から2つ目の行動を読む。(2) commitフェイズに限り、行動がどこにも無く
  `strategy.vote_plan`がYES/NOのどちらかに一意に決まる場合だけ、その
  票として読む。本番の試合（t12_r4_4401b）のdeepseek席の無効な応答17件の
  うち、(1)が6件・(2)が4件で救われ、残り7件（交渉で行動がどこにも無い形）
  は推測の余地があるため救済しない。
- `tests/test_no_real_contacts.py`に、実行時の環境のホームのパス
  （`Path.home()`から取得、コードに直書きしない）が書かれていないかの
  検査を追加した。`doc/viewer_operations.md`の2行を`~/shousuu-ketsu`の
  書き方に直してから足した。
- `tests/test_devlog_no_coauthor.py`で、devlogに`Co-Authored-By`の
  git trailerが無いことを固定した。既存devlog2件（2026-10-11_224741.md・
  2026-10-12_075200.md）にまぎれていた行を消した。

## 注8: サイクル5.0aで見つかった不具合（#78の抜け）

本番の試合`t12_r4_4501b`が、AIが出したpool_shareの提案（`round_num`が1つ
書き添えられていた）で止まった。`validate_terms`は仕様どおり無視して
通したが、`engine/contracts.py::create_contract`が書かれた値をそのまま
`Obligation`へ渡していたため、`Obligation`の内部検査（山分けはこれらを
持たない）に落ちて例外になった。#78は`validate_terms`だけを直接呼ぶ
単体テストで、この先（契約を作る処理）の抜けを検出できなかった。

直したのは`create_contract`がpool_shareの項から`obligor`・
`counterparty`・`round_num`・`vote_num`と、`details`の`condition`を
実際に捨てるようにしたこと（`Obligation`の内部検査にも`vote_num`を
足した）。#78に試合を通す形のテストを足し、同じ抜けが再発しないように
した。

あわせて見つけた小さな不一致2点も直した: (1) 型Bの投票先を小文字
（`"yes"`等）で書くと、`llm/response_parser.py`の層では通るのに、
`engine/contracts.py::validate_terms`の完全一致チェックで不成立になる
（`vote_commit`の`vote_raw`処理と同じく大文字にそろえて先へ渡すように
直した）。(2) `_validate_type_c_term_shape`の案内文「share_percentが
使えるのはwins_roundだけです」が、v0.5で`pool_share`も使えるようになった
ことを反映していなかった（到達しないが紛らわしいため文言をそろえた）。

交渉の1アクション（AIへの問い合わせ・形式検証・実行）で想定外の例外が
起きても試合全体を止めないための備えも入れた（`Game`の
`continue_on_action_error`引数。既定False=そのまま上げる。実際のAIを使う
試合（`scripts/llm_trial.py`）だけTrueにする）。記録は3つ（標準エラー
出力・イベントログ・本人への理由）残し、Bot検証・テストでは既定のまま
想定外の不具合にすぐ気づける形を保っている。

## 既存エンジンテストとの関係

受け入れテスト78件に加えて、エンジンの単体テスト（`test_vote_round.py`・
`test_settlement_v0_4.py`・`test_contracts.py`・`test_event_visibility.py`等）
と、追加確認3点（保存則・再現性・投票回数の上限、`test_invariants.py`）で
エンジン全体の整合性を確認している。テスト件数の内訳は devlog を参照。

`tests/test_public_disclosure.py`には#51〜#54に加えて、横断テスト2本
（`test_every_public_event_type_has_a_visible_state_field`・
`test_public_event_payloads_reach_players`）がある。公開（public）と区分した
イベント種別ごとに、対応する情報が`visible_state`のどの項目に入るかを表で
固定し、公開のイベントを新設してvisible_stateへの反映を忘れた場合に機械的に
検出できるようにしたもの（サイクル4.1で見つかった「公示の渡し漏れ」と同じ
種類の事故の再発防止、CLAUDE.md落とし穴⑩参照）。
