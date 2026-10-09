# 受け入れテスト対応表（v0.4 §12.3、サイクル4.0）

仕様書 `doc/uso8000000_shousuu_ketsu_spec_v0_4.md` §12.3 の50件すべてに対応する
pytest関数の一覧。命名は既存コードベース（v0.3）の慣例
（`test_acceptance_{番号}_{内容}`）に揃えた。#47・#48はエンジンの範囲外
（出題AI・履歴ファイル）のため、理由つきでskipしている（サイクル4.2送り）。

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
| 42 | 支払える上限100万。成立順1の型B違約金100万と成立順7の型Aが同じ精算 | `tests/test_settlement_v0_4.py::test_acceptance_42_partial_type_b_penalty_before_type_a` | 合格（読み替え。下記注1） |
| 43 | 契約が成立した巡の終わり | `tests/test_secrecy_contracts.py::test_acceptance_43_non_party_state_has_no_contract_trace`（公示の本数のみであることは`tests/test_secrecy_contracts.py::test_turn_disclosure_contains_only_count_no_names`でも確認） | 合格 |
| 44 | 署名がそろわない提案 | `tests/test_contracts.py::test_acceptance_44_unsigned_proposal_expires_at_vote_end` | 合格 |
| 45 | 交渉の巡 | `tests/test_game_loop.py::test_acceptance_45_negotiation_turn_limits_by_vote_kind` | 合格 |
| 46 | 時間切れ。その投票に型B「YES」指定あり | `tests/test_autocommit_type_b.py::test_acceptance_46_timeout_with_type_b_yes_instruction_auto_commits_yes` | 合格 |
| 47 | 出題AIの呼び出し失敗 | `tests/test_questions.py::test_acceptance_47_adapter_failure_falls_back_to_pool_avoiding_recent_30` | **skip（理由: サイクル4.2。下記注2）** |
| 48 | 試合で使った質問が9問 | `tests/test_questions.py::test_acceptance_48_only_used_9_questions_appended_to_history`（エンジン側の対応する範囲は`tests/test_game_loop.py::test_on_question_published_called_only_for_votes_actually_used`で確認済み） | **skip（理由: サイクル4.2。下記注2）** |
| 49 | R2のFinance終了後 | `tests/test_rank.py::test_acceptance_49_r2_public_ranks_names_only_same_amount_same_rank` | 合格 |
| 50 | 借入額の選択中 | `tests/test_game_loop.py::test_acceptance_50_choose_loan_cannot_see_others` | 合格 |

## 集計

- 合格: 48件
- skip（理由つき、サイクル4.2へ送付）: #47・#48 の2件
- 1件も省いていない（#1〜#50がすべて表に存在する）

## 注1: #42の読み替え（プラン§1 ★19）

仕様書の#42は原文「成立順1の型B違約金100万と、成立順7の**型A**100万が同じ精算」だが、
v0.4は精算が「投票の精算」（§7.3、型B違約金はここ）と「ラウンドの精算」（§7.4、型Aはここ）
の2段に分かれ、支払える上限（手順5/手順4）も精算ごとに別々に固定されるため、
型Bの違約金と型Aが同じ精算に来ることは構造的にない。

本テストは、同じ投票の精算に「成立順1の型B違約金100万」と「成立順7の型C
（同じ投票を対象にしたin_minority、100万）」を置くことで、「同じ決済・
成立順の優先」という#42の検証意図（上限に収まる義務だけが支払われ、
以降は0円になる）を保ったまま確かめている。仕様書の表現自体は変更していない
（完了報告の仕様書修正候補として報告する）。

## 注2: #47・#48をskipにした理由

- #47（出題AIの呼び出し失敗→予備リストから直近30問を避けて24問を選ぶ）と
  #48（試合で使った質問だけを履歴ファイルに追記する）は、出題AIの呼び出し・
  予備質問リストの拡張（60問以上）・`data/question_history.jsonl`への
  追記という、いずれも`llm/questions.py`の責務であり、このサイクルの範囲
  （エンジンと受け入れテストまで）の外にある。
- エンジン側が持つべき最小限の役割（「24問を受け取り、投票ごとに順に
  1問使う」）は実装・確認済み（`engine/game.py`の`questions_per_game`検証・
  `_question_cursor`・`on_question_published`フック）。#48の前段にあたる
  「実際に使った投票の回数だけ質問が公開される」ことは
  `tests/test_game_loop.py::test_on_question_published_called_only_for_votes_actually_used`
  で確認している。
- `llm/questions.py`自体のテスト（`tests/test_questions.py`、24件）は
  v0.3の値（QUESTION_COUNT=12・直近10問）のままで全て合格しており、
  v0.4の値（24問・直近30問）への変更はサイクル4.2で行う。

## 既存エンジンテストとの関係

受け入れテスト50件に加えて、エンジンの単体テスト（`test_vote_round.py`・
`test_settlement_v0_4.py`・`test_contracts.py`・`test_event_visibility.py`等）
と、追加確認3点（保存則・再現性・投票回数の上限、`test_invariants.py`）で
エンジン全体の整合性を確認している。テスト件数の内訳は devlog を参照。
