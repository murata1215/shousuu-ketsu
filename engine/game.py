"""
ゲームループモジュール（§4/§7、v0.4: 勝ち抜き制 L12R4V6）

サイクル4.0でv0.3から全面改訂した。v0.3は「1ラウンド＝1投票」の単層構造
だったが、v0.4は「ラウンド（R1〜R4）の中に投票（V1〜V6）が複数回入る」
二重構造（§1.1）になった。ラウンドの流れは

  ラウンド開始（全員が残っている人に戻る・参加費徴収・山の公開、§4.1/§7.1）
    → 投票を繰り返す（Open→Negotiation→Commit→Settlement、§7.2/§7.3）
      → 決着して残り2人以下、または打ち切り（やり直し連続3回）でラウンド終了
    → ラウンドの精算（山の支払い・型C(wins_round)・型A、§7.4）
    → Finance（利息。R2のみ全員の順位を公開、§7.6）

で、投票の判定（engine/vote.py）・精算（engine/settlement.py::settle_vote/
settle_round）・山の計算（engine/round.py）に分けて実装した。

dangou-card `engine/game.py`（B分類）の並列化3段構えパターン
（A: 可視状態構築等は逐次 → B: agent呼び出しだけThreadPoolExecutorで並列
→ C: 状態反映・ログ記録は逐次）はv0.3から引き続き使う（借入選択・投票・
振り返りに適用。交渉は1人のアクションが次の手番の可視状態に影響するため
並列化しない）。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from engine.autocommit import decide_auto_vote
from engine.config import GameConfig
from engine.events import EventLogger
from engine.finance import execute_finance
from engine.models import (
    Action, BroadcastAction, Contract, ContractProposeAction, ContractSignAction,
    ContractStatus, DmAction, GameResult, PassAction, PlayerState,
    RepayAction, RoundOutcome, TransferAction, Vote, VoteOutcome,
)
from engine.negotiation import PlayerAgent
from engine.rng import GameRng
from engine.settlement import settle_round, settle_vote
from engine import actions as action_ops
from engine import contracts as contract_ops
from engine import player as player_ops
from engine import round as round_ops


class Game:
    """
    少数決ゲーム本体（v0.4: L12R4V6）

    Args:
        config: ゲーム設定
        agents: プレイヤーID → PlayerAgent の辞書（ちょうど config.num_players 人）
        seed: 乱数シード（同一seedで完全再現可能）
        logger: イベントロガー（Noneなら自動生成）
        questions: 試合全体で使う質問（§5）。長さは
            config.num_rounds * config.max_votes_per_round（既定24）と一致する
            必要がある。Noneなら仮の固定文言を生成する（出題AI本体は4.2）。
            質問は試合を通した連番で、ラウンド境地に関係なく順に使う
            （使われなかった残りは履歴に書かない、§5.1）。
        on_question_published: その投票の質問をOpenで公開した直後に
            (round_num, vote_num, question) で呼ばれるフック。実際の履歴
            追記（data/question_history.jsonl）は呼び出し側の責務。
        max_parallel_agents: 借入選択・投票・振り返り・試合後振り返りで
            同時に呼び出すエージェント数の上限。既定1は逐次実行。
        stop_after_round: 指定時、このラウンドの処理を終えたら
            config.num_rounds 前でも実行を打ち切る（「実行だけ」の打ち切り。
            is_finalはconfig.num_rounds基準のまま）。
    """

    def __init__(
        self,
        config: GameConfig,
        agents: dict[str, PlayerAgent],
        seed: int = 42,
        logger: EventLogger | None = None,
        questions: list[str] | None = None,
        on_question_published: Callable[[int, int, str], None] | None = None,
        max_parallel_agents: int = 1,
        stop_after_round: int | None = None,
    ) -> None:
        if len(agents) != config.num_players:
            raise ValueError(
                f"agents count {len(agents)} != config.num_players {config.num_players}",
            )
        expected_questions = config.questions_per_game
        if questions is None:
            questions = [f"質問{i + 1}（仮）" for i in range(expected_questions)]
        elif len(questions) != expected_questions:
            raise ValueError(
                f"questions length {len(questions)} != questions_per_game {expected_questions}",
            )

        self.config = config
        self.agents = agents
        self.seed = seed
        self.rng = GameRng(seed)
        self.logger = logger or EventLogger()
        self.questions = list(questions)
        self.on_question_published = on_question_published
        self._max_parallel_agents = max_parallel_agents
        self._stop_after_round = stop_after_round

        self.players: dict[str, PlayerState] = {}
        self.carryover: int = 0
        self.current_round: int = 0

        self.round_summaries: list[RoundOutcome] = []
        self.total_interest: int = 0
        self.total_destroyed_pot: int = 0
        self.total_forfeited_remainder: int = 0

        self.contracts: list[Contract] = []
        """全契約（PROPOSED/ACTIVE/EXPIRED、§6.1）。取り消しは無いので要素は減らない"""

        self._next_contract_seq: int = 1
        """次に成立する契約へ振る通し番号（§6.1: 提案順ではなく成立順）"""

        self._question_cursor: int = 0
        """次に使う質問のインデックス（試合を通した連番、§5.1）"""

        self._remaining_ids: set[str] = set()
        self._eliminated_ids: set[str] = set()
        self._consecutive_ties: int = 0
        self._pot: int = 0

        self._round_vote_messages: list[dict[str, Any]] = []
        self._round_established_seqs: list[int] = []
        self._current_vote_auto_ids: set[str] = set()
        self._last_action_error: dict[str, str] = {}

        self._last_vote_outcome: VoteOutcome | None = None
        self._last_round_outcome: RoundOutcome | None = None
        self._public_ranks_history: dict[int, dict[str, int]] = {}
        self.post_game_reflections: dict[str, dict[str, Any]] = {}
        """player_id -> post_game_reflect()の戻り値（§9.4）"""

    # ------------------------------------------------------------------
    # メインループ
    # ------------------------------------------------------------------

    def run(self) -> GameResult:
        """
        ゲームを実行する（§4/§7: num_roundsラウンドを順に処理し、最終結果を返す）
        """
        self._setup()

        stopped_early = False

        for round_num in range(1, self.config.num_rounds + 1):
            self.current_round = round_num
            is_final_round = round_num == self.config.num_rounds

            self._run_round(round_num, is_final_round=is_final_round)
            self._phase_reflect(round_num)

            if self._stop_after_round is not None and round_num >= self._stop_after_round:
                stopped_early = round_num < self.config.num_rounds
                break

        result = self._finalize()
        if not stopped_early:
            self._phase_post_game_reflection(result)
            result = result.model_copy(
                update={"post_game_reflections": dict(self.post_game_reflections)},
            )
        return result

    def _run_round(self, round_num: int, *, is_final_round: bool) -> None:
        """
        1ラウンドを実行する（ラウンド開始→投票の繰り返し→ラウンドの精算→Finance、§4/§7）
        """
        carryover_in = self.carryover
        self._pot = self._phase_round_start(round_num, carryover_in)
        self._remaining_ids = set(self.players.keys())
        self._eliminated_ids = set()
        self._consecutive_ties = 0

        votes_in_round: list[VoteOutcome] = []
        round_auto_ids: set[str] = set()
        round_violator_ids: set[str] = set()
        round_shortfall_ids: set[str] = set()
        self._round_established_seqs = []

        vote_num = 0
        round_over = False
        while not round_over:
            vote_num += 1
            if vote_num > self.config.max_votes_per_round:
                # §4.4の決まりにより1ラウンドの投票は最大6回。12人設定では
                # 構造的に起きないはずの状態（§11.2方針2.a。CLAUDE.md:
                # 未定義の状態を黙って処理しない）。
                raise AssertionError(
                    f"R{round_num}: vote_num exceeded max_votes_per_round "
                    f"({self.config.max_votes_per_round})",
                )

            max_turns = self._negotiation_max_turns(vote_num, votes_in_round)

            self._phase_open(round_num, vote_num)
            self._phase_negotiation(round_num, vote_num, max_turns)
            self._assert_cash_non_negative(round_num, vote_num, "negotiation")
            votes = self._phase_commit(round_num, vote_num)
            self._assert_cash_non_negative(round_num, vote_num, "commit")

            outcome, violator_ids, shortfall_ids = self._phase_vote_settlement(
                round_num, vote_num, votes, self._consecutive_ties,
            )
            self._assert_cash_non_negative(round_num, vote_num, "settlement")

            votes_in_round.append(outcome)
            round_auto_ids |= self._current_vote_auto_ids
            round_violator_ids |= set(violator_ids)
            round_shortfall_ids |= set(shortfall_ids)
            self._consecutive_ties = outcome.consecutive_ties_after

            if outcome.result == "decisive":
                self._eliminated_ids |= set(outcome.eliminated_ids)
                self._remaining_ids = set(outcome.remaining_ids)

            self._last_vote_outcome = outcome
            round_over = outcome.round_over

        aborted = votes_in_round[-1].result == "abort"
        round_shortfall_ids |= set(
            self._phase_round_settlement(
                round_num, votes_in_round, carryover_in, self._pot,
                aborted=aborted, is_final_round=is_final_round,
            ),
        )
        self._assert_cash_non_negative(round_num, None, "round_settlement")

        self._phase_finance(
            round_num, votes_in_round,
            auto_commit_ids=round_auto_ids,
            type_b_violator_ids=round_violator_ids,
            payment_shortfall_ids=round_shortfall_ids,
        )
        self._assert_cash_non_negative(round_num, None, "finance")

    def _negotiation_max_turns(
        self, vote_num: int, votes_in_round: list[VoteOutcome],
    ) -> int:
        """
        交渉の巡の上限を決める（§7.2）

        ラウンド最初の投票（V1）は常に最大。V2以降は、直前の投票が決着なら
        「決着の後の投票」、やり直しなら「やり直しの再投票」の上限を使う。
        """
        if vote_num == 1:
            return self.config.negotiation_max_turns_first
        previous = votes_in_round[-1]
        if previous.result == "decisive":
            return self.config.negotiation_max_turns_next
        return self.config.negotiation_max_turns_retry

    def _phase_reflect(self, round_num: int) -> None:
        """
        ラウンド終了後の振り返り（§9.4）を全員へ呼ぶ。

        3段構え: A. 可視状態の構築は逐次、B. agent.reflect()の呼び出しは
        max_parallel_agents>1ならThreadPoolExecutorで並列、C. reflect()は
        戻り値を返さずログも出さないため、Cで行うことは無い。
        """
        last_vote_num = self._last_vote_outcome.vote_num if self._last_vote_outcome else 1
        pending = [
            (pid, self.players[pid], self._build_visible_state(
                round_num, last_vote_num, for_player_id=pid,
            ))
            for pid in sorted(self.agents)
        ]

        def _call(pid: str, player: PlayerState, visible_state: dict[str, Any]) -> None:
            self.agents[pid].reflect(player, round_num, visible_state)

        if self._max_parallel_agents > 1 and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=self._max_parallel_agents) as pool:
                futures = [pool.submit(_call, pid, player, vs) for pid, player, vs in pending]
                for future in as_completed(futures):
                    future.result()
        else:
            for pid, player, vs in pending:
                _call(pid, player, vs)

    def _phase_post_game_reflection(self, result: GameResult) -> None:
        """
        試合完全終了後の振り返り（§9.4）を全員へ1回だけ呼ぶ。

        3段構えはrun()の_phase_reflectと同じ。失敗は記録のみで試合結果には
        一切影響しない。
        """
        ranks = player_ops.assets_ranking(self.players.values())
        pending = sorted(self.agents)

        def _call(pid: str) -> dict[str, Any] | None:
            r = ranks[pid]
            context = {
                "own_rank": r.rank,
                "own_rank_tied": r.tied,
                "final_assets": result.final_assets.get(pid),
            }
            try:
                return self.agents[pid].post_game_reflect(context)
            except Exception:
                return None

        if self._max_parallel_agents > 1 and len(pending) > 1:
            results: dict[str, dict[str, Any] | None] = {}
            with ThreadPoolExecutor(max_workers=self._max_parallel_agents) as pool:
                futures = {pool.submit(_call, pid): pid for pid in pending}
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
        else:
            results = {pid: _call(pid) for pid in pending}

        for pid in pending:
            comment = results.get(pid)
            if comment is None:
                continue
            self.post_game_reflections[pid] = comment
            self.logger.log(
                "POST_GAME_REFLECTION", self.config.num_rounds, "finance",
                visibility="spectator", data={"player_id": pid, **comment},
            )

    def _assert_cash_non_negative(
        self, round_num: int, vote_num: int | None, phase: str,
    ) -> None:
        """現金がマイナスにならないことの内部不変条件チェック（§3.2/§7.3手順7）"""
        negative = [p.player_id for p in self.players.values() if p.cash < 0]
        if negative:
            raise AssertionError(
                f"R{round_num}V{vote_num} {phase}: cash went negative for {sorted(negative)}",
            )

    # ------------------------------------------------------------------
    # セットアップ（§3.1: 借入額の選択）
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """
        全員が同時に借入額を選び、決定がそろった後に一斉公開する（§3.1）
        """
        self.logger.log(
            "GAME_START", 0, "setup", visibility="public",
            data={
                "num_players": self.config.num_players,
                "num_rounds": self.config.num_rounds,
                "max_votes_per_round": self.config.max_votes_per_round,
                "seed": self.seed,
            },
        )

        pids = sorted(self.agents)

        def _call(pid: str) -> int:
            return self.agents[pid].choose_loan(self.config)

        if self._max_parallel_agents > 1 and len(pids) > 1:
            raw_loans: dict[str, int] = {}
            with ThreadPoolExecutor(max_workers=self._max_parallel_agents) as pool:
                futures = {pool.submit(_call, pid): pid for pid in pids}
                for future in as_completed(futures):
                    raw_loans[futures[future]] = future.result()
        else:
            raw_loans = {pid: _call(pid) for pid in pids}

        loans: dict[str, int] = {}
        for pid in pids:
            loan = max(self.config.loan_min, min(self.config.loan_max, raw_loans[pid]))
            loans[pid] = loan

        for pid, loan in loans.items():
            self.players[pid] = player_ops.create_player(pid, loan)

        self.logger.log(
            "LOAN_REVEALED", 0, "setup", visibility="public",
            data={"loans": dict(sorted(loans.items()))},
        )

    # ------------------------------------------------------------------
    # ラウンド開始（§4.1/§7.1）
    # ------------------------------------------------------------------

    def _phase_round_start(self, round_num: int, carryover_in: int) -> int:
        """
        12人全員を「残っている人」に戻し、参加費を徴収して山を公開する（§4.1）
        """
        for pid in sorted(self.players):
            new_p, paid, borrowed, _shortfall = player_ops.pay_or_borrow(
                self.players[pid], self.config.entry_fee, self.config, cap_exempt=True,
            )
            self.players[pid] = new_p
            self.logger.log(
                "ENTRY_FEE_COLLECTED", round_num, "open", visibility="self",
                data={"player_id": pid, "paid": paid, "borrowed": borrowed},
            )

        pot = round_ops.initial_pot(self.config, carryover_in)
        self.logger.log(
            "ROUND_START", round_num, "open", visibility="public",
            data={"carryover_in": carryover_in, "pot": pot},
        )
        self.logger.log(
            "POT_UPDATED", round_num, "open", visibility="public",
            data={"pot": pot, "reason": "round_start"},
        )
        return pot

    # ------------------------------------------------------------------
    # Phase 1: Open（§7.2手順1）
    # ------------------------------------------------------------------

    def _phase_open(self, round_num: int, vote_num: int) -> None:
        """
        質問・残っている人・退場者・山の額・やり直しの連続回数を公開し、
        本人にだけ財務通知を送る（§7.2）。
        """
        question = self.questions[self._question_cursor]
        self._question_cursor += 1

        self.logger.log(
            "VOTE_OPEN", round_num, "open", vote_num=vote_num, visibility="public",
            data={
                "question": question,
                "remaining_ids": sorted(self._remaining_ids),
                "eliminated_ids": sorted(self._eliminated_ids),
                "pot": self._pot,
                "consecutive_ties": self._consecutive_ties,
            },
        )
        if self.on_question_published is not None:
            self.on_question_published(round_num, vote_num, question)

        ranks = player_ops.assets_ranking(self.players.values())
        for pid in sorted(self.players):
            r = ranks[pid]
            self.logger.log(
                "RANK_NOTIFIED", round_num, "open", vote_num=vote_num, visibility="self",
                data={"player_id": pid, "rank": r.rank, "tied": r.tied, "n_players": r.n_players},
            )
            finance = self._build_my_finance(pid, round_num, vote_num)
            self.logger.log(
                "FINANCE_NOTICE", round_num, "open", vote_num=vote_num, visibility="self",
                data={"player_id": pid, **finance},
            )

    # ------------------------------------------------------------------
    # Phase 2: Negotiation（§7.2手順2）
    # ------------------------------------------------------------------

    def _phase_negotiation(self, round_num: int, vote_num: int, max_turns: int) -> None:
        """
        最大max_turns巡、毎巡ランダムな手番で1アクションを処理する（§7.2手順2）。

        12人全員（退場者も含む）が参加する。全員が続けてパスすればその巡で
        早期終了する。巡が尽きた後（早期終了も含む）、この投票で提案され
        署名がそろわなかった契約を失効させる（§6.1）。
        """
        self._round_vote_messages = []
        all_ids = sorted(self.players.keys())

        for turn in range(1, max_turns + 1):
            turn_order = self.rng.shuffle_turn_order(all_ids)
            all_passed = True
            established_this_turn = 0

            for pid in turn_order:
                p = self.players[pid]
                agent = self.agents[pid]
                visible_state = self._build_visible_state(round_num, vote_num, for_player_id=pid)
                action = agent.negotiate(p, round_num, vote_num, turn, visible_state)

                if isinstance(action, PassAction):
                    self.logger.log(
                        "NEGOTIATION_ACTION", round_num, "negotiation", vote_num=vote_num,
                        visibility="spectator",
                        data={"player_id": pid, "action": "pass", "turn": turn},
                    )
                    continue

                all_passed = False
                result = action_ops.validate_action(
                    action, p, self.config, self.players,
                    round_num=round_num, vote_num=vote_num, contracts=self.contracts,
                )
                if not result.success:
                    self._last_action_error[pid] = result.reason or ""
                    self.logger.log(
                        "NEGOTIATION_ACTION", round_num, "negotiation", vote_num=vote_num,
                        visibility="spectator",
                        data={
                            "player_id": pid, "action": action.type,
                            "success": False, "reason": result.reason, "turn": turn,
                        },
                    )
                    if isinstance(action, ContractProposeAction):
                        # 形式に合わない提案は提案者にのみ理由を返す（§9.3）
                        self.logger.log(
                            "CONTRACT_REJECTED", round_num, "negotiation", vote_num=vote_num,
                            visibility="self",
                            data={"player_id": pid, "reason": result.reason, "turn": turn},
                        )
                    continue

                self._last_action_error.pop(pid, None)
                established_this_turn += self._execute_negotiation_action(
                    action, pid, round_num, vote_num, turn,
                )

            self.logger.log(
                "TURN_CONTRACTS_ESTABLISHED", round_num, "negotiation", vote_num=vote_num,
                visibility="public", data={"turn": turn, "count": established_this_turn},
            )

            if all_passed:
                self.logger.log(
                    "NEGOTIATION_EARLY_END", round_num, "negotiation", vote_num=vote_num,
                    visibility="spectator", data={"turn": turn},
                )
                break

        self._expire_unsigned_contracts(round_num, vote_num)

    def _expire_unsigned_contracts(self, round_num: int, vote_num: int) -> None:
        """
        この投票で提案され署名がそろわなかった契約をEXPIREDにする
        （§6.1: 「提案した投票の締切で失効する」）
        """
        for i, c in enumerate(self.contracts):
            if (
                c.status == ContractStatus.PROPOSED
                and c.round_created == round_num
                and c.vote_created == vote_num
            ):
                self.contracts[i] = c.model_copy(update={"status": ContractStatus.EXPIRED})
                self.logger.log(
                    "CONTRACT_EXPIRED", round_num, "negotiation", vote_num=vote_num,
                    visibility="parties",
                    data={
                        "contract_id": c.contract_id, "proposer": c.proposer,
                        "parties": list(c.parties), "signed_by": list(c.signed_by),
                    },
                )

    def _execute_negotiation_action(
        self, action: Action, pid: str, round_num: int, vote_num: int, turn: int,
    ) -> int:
        """
        検証済みのNegotiationアクションを実行する

        Returns:
            この呼び出しで契約が新たに成立したら1、それ以外は0
            （§7.2: 「各巡の終わりに、その巡で成立した本数を公示する」の
            集計に使う）
        """
        if isinstance(action, DmAction):
            self._round_vote_messages.append({
                "type": "dm", "from": pid, "to": action.to,
                "message": action.message, "turn": turn,
            })
            self.logger.log(
                "NEGOTIATION_ACTION", round_num, "negotiation", vote_num=vote_num,
                visibility="parties",
                data={
                    "player_id": pid, "action": "dm", "to": action.to, "turn": turn,
                    "message": action.message,
                },
            )
            return 0

        if isinstance(action, BroadcastAction):
            self._round_vote_messages.append({
                "type": "broadcast", "from": pid, "to": None,
                "message": action.message, "turn": turn,
            })
            self.logger.log(
                "NEGOTIATION_ACTION", round_num, "negotiation", vote_num=vote_num,
                visibility="public",
                data={"player_id": pid, "action": "broadcast", "turn": turn, "message": action.message},
            )
            return 0

        if isinstance(action, TransferAction):
            self.players[pid] = player_ops.pay(self.players[pid], action.amount)
            self.players[action.to] = player_ops.receive(self.players[action.to], action.amount)
            self.logger.log(
                "TRANSFER", round_num, "negotiation", vote_num=vote_num, visibility="parties",
                data={"player_id": pid, "to": action.to, "amount": action.amount, "turn": turn},
            )
            return 0

        if isinstance(action, RepayAction):
            old_p = self.players[pid]
            new_p, actual = player_ops.repay(old_p, action.amount)
            self.players[pid] = new_p
            self.logger.log(
                "REPAYMENT", round_num, "negotiation", vote_num=vote_num, visibility="self",
                data={
                    "player_id": pid, "requested": action.amount, "amount": actual, "turn": turn,
                    "from_post": old_p.debt_post - new_p.debt_post,
                    "from_pre": old_p.debt_pre - new_p.debt_pre,
                },
            )
            return 0

        if isinstance(action, ContractProposeAction):
            parties = [pid] + list(action.with_players)
            existing_ids = {c.contract_id for c in self.contracts}
            contract_id = self.rng.random_contract_id(existing_ids)
            contract = contract_ops.create_contract(
                proposer=pid, parties=parties, terms=action.terms,
                round_created=round_num, vote_created=vote_num, contract_id=contract_id,
            )
            self.contracts.append(contract)
            self.logger.log(
                "CONTRACT_PROPOSED", round_num, "negotiation", vote_num=vote_num,
                visibility="parties",
                data={
                    "contract_id": contract.contract_id, "proposer": pid,
                    "parties": parties, "turn": turn,
                    "obligations": [ob.model_dump(mode="json") for ob in contract.obligations],
                },
            )
            return 0

        if isinstance(action, ContractSignAction):
            for i, c in enumerate(self.contracts):
                if c.contract_id != action.contract_id:
                    continue
                signed, just_established = contract_ops.sign_contract(c, pid)
                self.logger.log(
                    "CONTRACT_SIGNED", round_num, "negotiation", vote_num=vote_num,
                    visibility="parties",
                    data={"contract_id": action.contract_id, "signer": pid, "turn": turn},
                )
                if just_established:
                    seq = self._next_contract_seq
                    self._next_contract_seq += 1
                    signed = signed.model_copy(update={
                        "contract_seq": seq, "round_established": round_num,
                        "vote_established": vote_num,
                    })
                    self._round_established_seqs.append(seq)
                    self.logger.log(
                        "CONTRACT_ESTABLISHED", round_num, "negotiation", vote_num=vote_num,
                        visibility="parties",
                        data={
                            "contract_id": action.contract_id, "parties": list(signed.parties),
                            "contract_seq": seq,
                        },
                    )
                    self.contracts[i] = signed
                    return 1
                self.contracts[i] = signed
                break
        return 0

    # ------------------------------------------------------------------
    # Phase 3: Commit（§7.2手順3/§4.2/§4.6）
    # ------------------------------------------------------------------

    def _phase_commit(self, round_num: int, vote_num: int) -> dict[str, Vote]:
        """
        残っている人がYESかNOを秘密提出する（§4.2）。退場者には求めない。

        無効な出力は1回だけ再試行し、それでも駄目ならシステムが代行する（§4.6）。
        """
        pids = sorted(self._remaining_ids)
        pending = [
            (pid, self.players[pid], self._build_visible_state(round_num, vote_num, for_player_id=pid))
            for pid in pids
        ]

        def _attempt_commit(pid: str, p: PlayerState, visible_state: dict[str, Any]) -> Vote | None:
            agent = self.agents[pid]
            for _attempt in range(2):  # 本番1回＋再試行1回（§4.6）
                try:
                    candidate = agent.commit(p, round_num, vote_num, visible_state)
                except Exception:
                    candidate = None
                if isinstance(candidate, Vote):
                    return candidate
            return None

        if self._max_parallel_agents > 1 and len(pending) > 1:
            candidates: dict[str, Vote | None] = {}
            with ThreadPoolExecutor(max_workers=self._max_parallel_agents) as pool:
                futures = {
                    pool.submit(_attempt_commit, pid, p, vs): pid for pid, p, vs in pending
                }
                for future in as_completed(futures):
                    candidates[futures[future]] = future.result()
        else:
            candidates = {pid: _attempt_commit(pid, p, vs) for pid, p, vs in pending}

        votes: dict[str, Vote] = {}
        auto_ids: list[str] = []

        for pid in pids:
            vote = candidates[pid]

            if vote is None:
                constraint = contract_ops.vote_constraint_for_vote(
                    self.contracts, pid, round_num, vote_num,
                )
                vote = decide_auto_vote(self.rng, type_b_constraint=constraint)
                auto_ids.append(pid)
                self.logger.log(
                    "AUTO_COMMIT", round_num, "commit", vote_num=vote_num, visibility="public",
                    data={
                        "player_id": pid, "vote": vote.value,
                        "source": "type_b" if constraint is not None else "random",
                    },
                )

            votes[pid] = vote
            self.logger.log(
                "COMMIT", round_num, "commit", vote_num=vote_num, visibility="self",
                data={"player_id": pid, "vote": vote.value},
            )

        self._current_vote_auto_ids = set(auto_ids)
        return votes

    # ------------------------------------------------------------------
    # Phase 4: 投票の精算（§7.3）
    # ------------------------------------------------------------------

    def _phase_vote_settlement(
        self, round_num: int, vote_num: int, votes: dict[str, Vote], consecutive_ties_before: int,
    ) -> tuple[VoteOutcome, list[str], list[str]]:
        result = settle_vote(
            self.players, votes, self.config, round_num, vote_num, consecutive_ties_before,
            self.logger, contracts=self.contracts,
        )
        self.players = result.players
        self._pot += result.outcome.extension_fee_collected
        if result.outcome.extension_fee_collected > 0:
            self.logger.log(
                "POT_UPDATED", round_num, "settlement", vote_num=vote_num, visibility="public",
                data={"pot": self._pot, "reason": "extension_fee"},
            )

        violator_ids = sorted({obligor for obligor, _ in result.report.violations})
        return result.outcome, violator_ids, result.report.shortfall_ids

    # ------------------------------------------------------------------
    # Phase 5: ラウンドの精算（§7.4）
    # ------------------------------------------------------------------

    def _phase_round_settlement(
        self,
        round_num: int,
        votes_in_round: list[VoteOutcome],
        carryover_in: int,
        pot_final: int,
        *,
        aborted: bool,
        is_final_round: bool,
    ) -> list[str]:
        result = settle_round(
            self.players, self.config, round_num, votes_in_round, carryover_in, pot_final,
            aborted, self.logger, is_final_round=is_final_round, contracts=self.contracts,
        )
        self.players = result.players
        self.carryover = result.outcome.carryover_out
        self.total_destroyed_pot += result.outcome.destroyed_pot
        self.total_forfeited_remainder += result.outcome.forfeited_remainder
        self._last_round_outcome = result.outcome
        return result.report.shortfall_ids

    # ------------------------------------------------------------------
    # Phase 6: Finance（§7.4手順8/§3.3/§7.6）
    # ------------------------------------------------------------------

    def _phase_finance(
        self,
        round_num: int,
        votes_in_round: list[VoteOutcome],
        *,
        auto_commit_ids: set[str],
        type_b_violator_ids: set[str],
        payment_shortfall_ids: set[str],
    ) -> None:
        updated, interest_total = execute_finance(self.players, round_num, self.config, self.logger)
        self.players = updated
        self.total_interest += interest_total

        assert self._last_round_outcome is not None
        summary = self._last_round_outcome.model_copy(update={
            "votes": list(votes_in_round),
            "interest_total": interest_total,
            "auto_commit_ids": sorted(auto_commit_ids),
            "established_contract_seqs": sorted(self._round_established_seqs),
            "type_b_violator_ids": sorted(type_b_violator_ids),
            "payment_shortfall_ids": sorted(payment_shortfall_ids),
        })

        if round_num in self.config.rank_public_rounds:
            ranks = player_ops.assets_ranking(self.players.values())
            public_ranks = {pid: r.rank for pid, r in ranks.items()}
            summary = summary.model_copy(update={"public_ranks": public_ranks})
            self._public_ranks_history[round_num] = public_ranks
            self.logger.log(
                "RANK_PUBLISHED", round_num, "finance", visibility="public",
                data={"ranks": dict(sorted(public_ranks.items()))},
            )

        self.round_summaries.append(summary)
        self._last_round_outcome = summary

    # ------------------------------------------------------------------
    # 終了処理
    # ------------------------------------------------------------------

    def _finalize(self) -> GameResult:
        final_assets = {pid: p.net_assets for pid, p in self.players.items()}
        ranks = player_ops.assets_ranking(self.players.values())
        final_ranks = {pid: r.rank for pid, r in ranks.items()}

        self.logger.log(
            "GAME_END", self.config.num_rounds, "finance", visibility="public",
            data={
                "final_assets": dict(sorted(final_assets.items())),
                "final_ranks": dict(sorted(final_ranks.items())),
            },
        )

        return GameResult(
            seed=self.seed,
            final_players=dict(self.players),
            final_assets=final_assets,
            final_ranks=final_ranks,
            round_summaries=list(self.round_summaries),
            total_interest=self.total_interest,
            total_destroyed_pot=self.total_destroyed_pot,
            total_forfeited_remainder=self.total_forfeited_remainder,
        )

    # ------------------------------------------------------------------
    # 公開情報の構築（§8）
    # ------------------------------------------------------------------

    def _visible_messages(self, for_player_id: str) -> list[dict[str, Any]]:
        """
        この投票のメッセージのうち、for_player_id に見える分だけを返す
        （§8: 全体発言は公開、DMは当事者のみ）
        """
        visible = []
        for m in self._round_vote_messages:
            if m["type"] == "broadcast":
                visible.append(m)
            elif m["type"] == "dm" and for_player_id in (m["from"], m["to"]):
                visible.append(m)
        return visible

    def _build_my_finance(self, player_id: str, round_num: int, vote_num: int) -> dict[str, Any]:
        """
        本人だけに届く財務通知の内容を構築する（§7.5）

        現金・借金残高（2種別）・今ラウンドの利息見込み・残り借入枠・
        この投票とこのラウンドを対象にした自分の義務を含む。
        """
        me = self.players[player_id]
        interest_pre_forecast = -(
            -me.debt_pre * self.config.interest_rate_pre_num // self.config.interest_rate_pre_den
        )
        interest_post_forecast = -(
            -me.debt_post * self.config.interest_rate_post_num // self.config.interest_rate_post_den
        )
        due_vote = contract_ops.obligations_due(self.contracts, round_num, vote_num)
        due_round = contract_ops.obligations_due(self.contracts, round_num, None)
        obligations_due = [
            {
                "ob_type": ob.ob_type.value, "counterparty": ob.counterparty,
                "round_num": ob.round_num, "vote_num": ob.vote_num,
                "details": dict(ob.details),
            }
            for ob in due_vote + due_round
            if ob.obligor == player_id
        ]
        return {
            "cash": me.cash,
            "debt_pre": me.debt_pre,
            "debt_pre_repayable": False,
            "debt_pre_note": "返済不可（開始前の借金・5%）",
            "debt_post": me.debt_post,
            "total_debt": me.total_debt,
            "remaining_credit": player_ops.remaining_credit(me, self.config),
            "interest_forecast": interest_pre_forecast + interest_post_forecast,
            "obligations_due": obligations_due,
        }

    def _build_visible_state(
        self, round_num: int, vote_num: int, for_player_id: str | None = None,
    ) -> dict[str, Any]:
        """
        エージェントに渡す公開情報の辞書を構築する（§8）

        秘匿情報（現金・借金残高・残り借入枠・他人の順位・DM・締切前の投票先・
        契約の当事者・内容・成立順）は for_player_id 本人（当事者）の分のみ
        含め、他プレイヤーには一切渡さない。v0.4では契約の存在そのものが
        非公開になったため（§1.2/§8）、v0.3にあった`contracts_public`
        （全員に見える契約一覧）は廃止した。
        """
        last_vote = self._last_vote_outcome
        last_round = self._last_round_outcome

        state: dict[str, Any] = {
            "round_num": round_num,
            "vote_num": vote_num,
            "question": self.questions[self._question_cursor - 1] if self._question_cursor else None,
            "pot": self._pot,
            "remaining_ids": sorted(self._remaining_ids),
            "eliminated_ids": sorted(self._eliminated_ids),
            "consecutive_ties": self._consecutive_ties,
            "initial_loans": {pid: p.initial_loan for pid, p in sorted(self.players.items())},
            "public_ranks_history": dict(self._public_ranks_history),
            "last_vote_result": None if last_vote is None else {
                "round_num": last_vote.round_num,
                "vote_num": last_vote.vote_num,
                "result": last_vote.result,
                "yes_ids": last_vote.yes_ids,
                "no_ids": last_vote.no_ids,
                "eliminated_ids": last_vote.eliminated_ids,
                "remaining_ids": last_vote.remaining_ids,
                "minority_side": last_vote.minority_side.value if last_vote.minority_side else None,
            },
            "last_round_result": None if last_round is None else {
                "round_num": last_round.round_num,
                "aborted": last_round.aborted,
                "winner_ids": last_round.winner_ids,
                "payout_per_winner": last_round.payout_per_winner,
                "carryover_out": last_round.carryover_out,
                "auto_commit_ids": last_round.auto_commit_ids,
                "type_b_violator_ids": last_round.type_b_violator_ids,
                "payment_shortfall_ids": last_round.payment_shortfall_ids,
            },
        }

        if for_player_id is not None:
            state["messages"] = self._visible_messages(for_player_id)
            state["my_last_action_error"] = self._last_action_error.get(for_player_id)

            def _obligations_view(c: Contract) -> list[dict[str, Any]]:
                return [
                    {
                        "obligor": ob.obligor, "counterparty": ob.counterparty,
                        "ob_type": ob.ob_type.value, "round_num": ob.round_num,
                        "vote_num": ob.vote_num, "details": dict(ob.details),
                    }
                    for ob in c.obligations
                ]

            state["my_contracts"] = [
                {
                    "contract_id": c.contract_id, "contract_seq": c.contract_seq,
                    "parties": list(c.parties), "obligations": _obligations_view(c),
                }
                for c in self.contracts
                if c.status == ContractStatus.ACTIVE and for_player_id in c.parties
            ]
            state["contracts_pending"] = [
                {
                    "contract_id": c.contract_id, "proposer": c.proposer,
                    "parties": list(c.parties), "signed_by": list(c.signed_by),
                    "round_created": c.round_created, "vote_created": c.vote_created,
                    "obligations": _obligations_view(c),
                }
                for c in self.contracts
                if c.status == ContractStatus.PROPOSED and for_player_id in c.parties
            ]

            if for_player_id in self.players:
                state["my_finance"] = self._build_my_finance(for_player_id, round_num, vote_num)
                my_rank = player_ops.assets_ranking(self.players.values())[for_player_id]
                state["my_rank"] = {
                    "rank": my_rank.rank, "tied": my_rank.tied, "n_players": my_rank.n_players,
                }

        return state
