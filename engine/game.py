"""
ゲームループモジュール（§7 ラウンド進行）

dangou-card `engine/game.py`（B分類）の5フェイズ構成（Open → Negotiation →
Commit → Settlement → Finance）を踏襲しつつ、脱落・市場・カード・報奨・
匿名通信・倍掛け・反省フェイズを全て削除した。契約（§6）は未実装で、
Settlement の契約フックは常に空（engine/settlement.py参照）。

投票の収集は「全員から集めてから結果を公開する」（§4.1: 締切後に変更不可、
§7.1手順1でReveal）設計にしているため、1人のCommitが他人に見える経路は
存在しない。借入額の選択（§3.1）も同様に、全員分を集め切ってから
`initial_loans` として公開する。
"""

from typing import Any

from engine.autocommit import decide_auto_vote
from engine.config import GameConfig
from engine.events import EventLogger
from engine.finance import execute_finance
from engine.models import (
    Action, BroadcastAction, DmAction, GameResult, PassAction, PlayerState,
    RepayAction, RoundSummary, TransferAction, Vote, VoteCommitAction,
)
from engine.negotiation import PlayerAgent
from engine.rng import GameRng
from engine.settlement import execute_settlement
from engine import actions as action_ops
from engine import player as player_ops


class Game:
    """
    少数決ゲーム本体

    Args:
        config: ゲーム設定
        agents: プレイヤーID → PlayerAgent の辞書（ちょうど config.num_players 人）
        seed: 乱数シード（同一seedで完全再現可能）
        logger: イベントロガー（Noneなら自動生成）
        questions: 各ラウンドの質問（§5）。Noneなら仮の固定文言を生成する。
            指定時は長さが config.num_rounds と一致しなければ ValueError
            （質問生成本体はサイクル1.2で実装する）
    """

    def __init__(
        self,
        config: GameConfig,
        agents: dict[str, PlayerAgent],
        seed: int = 42,
        logger: EventLogger | None = None,
        questions: list[str] | None = None,
    ) -> None:
        if len(agents) != config.num_players:
            raise ValueError(
                f"agents count {len(agents)} != config.num_players {config.num_players}",
            )
        if questions is None:
            questions = [f"質問{i + 1}（仮）" for i in range(config.num_rounds)]
        elif len(questions) != config.num_rounds:
            raise ValueError(
                f"questions length {len(questions)} != num_rounds {config.num_rounds}",
            )

        self.config = config
        self.agents = agents
        self.seed = seed
        self.rng = GameRng(seed)
        self.logger = logger or EventLogger()
        self.questions = list(questions)

        self.players: dict[str, PlayerState] = {}
        self.carryover: int = 0
        self.current_round: int = 0

        self.round_summaries: list[RoundSummary] = []
        self.total_interest: int = 0
        self.total_destroyed_carryover: int = 0
        self.total_forfeited_remainder: int = 0

        self._current_auto_pids: set[str] = set()
        self._round_messages: list[dict[str, Any]] = []
        self._last_round_summary: RoundSummary | None = None
        self._public_ranks_history: dict[int, dict[str, int]] = {}

    # ------------------------------------------------------------------
    # メインループ
    # ------------------------------------------------------------------

    def run(self) -> GameResult:
        """
        ゲームを実行する（§7: 12ラウンドを順に処理し、最終結果を返す）
        """
        self._setup()

        for round_num in range(1, self.config.num_rounds + 1):
            self.current_round = round_num
            is_final = round_num == self.config.num_rounds

            self._phase_open(round_num)
            self._phase_negotiation(round_num)
            self._assert_cash_non_negative(round_num, "negotiation")
            votes = self._phase_commit(round_num)
            self._assert_cash_non_negative(round_num, "commit")
            self._phase_settlement(round_num, votes, is_final=is_final)
            self._assert_cash_non_negative(round_num, "settlement")
            self._phase_finance(round_num)
            self._assert_cash_non_negative(round_num, "finance")

            for pid, agent in self.agents.items():
                agent.reflect(
                    self.players[pid], round_num,
                    self._build_visible_state(round_num, for_player_id=pid),
                )

        return self._finalize()

    def _assert_cash_non_negative(self, round_num: int, phase: str) -> None:
        """
        現金がマイナスにならないことの内部不変条件チェック（§3.2/§7.1手順7）

        pay_or_borrow・repay・settle_negative_cash は設計上いずれも現金を
        マイナスにしないが、将来の契約執行（1.1）がこの前提を壊していないか
        を早期に検出するための安全網として、各フェイズの末尾で確認する。
        """
        negative = [p.player_id for p in self.players.values() if p.cash < 0]
        if negative:
            raise AssertionError(
                f"R{round_num} {phase}: cash went negative for {sorted(negative)}",
            )

    # ------------------------------------------------------------------
    # セットアップ（§3.1: 借入額の選択）
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """
        全員が同時に借入額を選び、決定がそろった後に一斉公開する（§3.1）。

        `PlayerAgent.choose_loan()` は config しか受け取らないため、構造的に
        他プレイヤーの借入額を参照できない（§3.1「他のAIの借入額を見てから
        決めることはできない」を型レベルで保証する）。
        """
        self.logger.log("GAME_START", 0, "setup", data={
            "num_players": self.config.num_players,
            "num_rounds": self.config.num_rounds,
            "seed": self.seed,
        })

        loans: dict[str, int] = {}
        for pid, agent in self.agents.items():
            loan = agent.choose_loan(self.config)
            loan = max(self.config.loan_min, min(self.config.loan_max, loan))
            loans[pid] = loan

        for pid, loan in loans.items():
            self.players[pid] = player_ops.create_player(pid, loan)

        # 一斉公開（§3.1）
        self.logger.log("LOAN_REVEALED", 0, "setup", data={
            "loans": dict(sorted(loans.items())),
        })

    # ------------------------------------------------------------------
    # Phase 1: Open（§7手順1）
    # ------------------------------------------------------------------

    def _phase_open(self, round_num: int) -> None:
        """
        そのラウンドの質問と持ち越し額を公開し、本人にだけ財務通知を送る
        （§7.2）。財務通知は visible_state の生成時に計算するため、ここでは
        ログと公開イベントのみ発行する。
        """
        self.logger.log("ROUND_OPEN", round_num, "open", data={
            "question": self.questions[round_num - 1],
            "carryover": self.carryover,
        })

        ranks = player_ops.assets_ranking(self.players.values())
        for pid in sorted(self.players):
            r = ranks[pid]
            self.logger.log("RANK_NOTIFIED", round_num, "open", data={
                "player_id": pid, "rank": r.rank, "tied": r.tied, "n_players": r.n_players,
            })

    # ------------------------------------------------------------------
    # Phase 2: Negotiation（§7手順2）
    # ------------------------------------------------------------------

    def _phase_negotiation(self, round_num: int) -> None:
        """
        最大10巡、毎巡ランダムな手番で1アクションを処理する（§7手順2）。

        全員が続けてパスすればその巡で早期終了する。
        """
        self._round_messages = []
        alive_ids = sorted(self.players.keys())

        for turn in range(1, self.config.negotiation_max_turns + 1):
            turn_order = self.rng.shuffle_turn_order(alive_ids)
            all_passed = True

            for pid in turn_order:
                p = self.players[pid]
                agent = self.agents[pid]
                visible_state = self._build_visible_state(round_num, for_player_id=pid)
                action = agent.negotiate(p, round_num, turn, visible_state)

                if isinstance(action, PassAction):
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "pass", "turn": turn,
                    })
                    continue

                all_passed = False
                result = action_ops.validate_action(action, p, self.config, self.players)
                if not result.success:
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": action.type,
                        "success": False, "reason": result.reason, "turn": turn,
                    })
                    continue

                self._execute_negotiation_action(action, pid, round_num, turn)

            if all_passed:
                self.logger.log("NEGOTIATION_EARLY_END", round_num, "negotiation", data={
                    "turn": turn,
                })
                break

    def _execute_negotiation_action(
        self, action: Action, pid: str, round_num: int, turn: int,
    ) -> None:
        """検証済みのNegotiationアクションを実行する"""
        if isinstance(action, DmAction):
            self._round_messages.append({
                "type": "dm", "from": pid, "to": action.to,
                "message": action.message, "turn": turn,
            })
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "dm", "to": action.to, "turn": turn,
            })
        elif isinstance(action, BroadcastAction):
            self._round_messages.append({
                "type": "broadcast", "from": pid, "to": None,
                "message": action.message, "turn": turn,
            })
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "broadcast", "turn": turn,
            })
        elif isinstance(action, TransferAction):
            self.players[pid] = player_ops.pay(self.players[pid], action.amount)
            self.players[action.to] = player_ops.receive(self.players[action.to], action.amount)
            self.logger.log("TRANSFER", round_num, "negotiation", data={
                "player_id": pid, "to": action.to, "amount": action.amount, "turn": turn,
            })
        elif isinstance(action, RepayAction):
            old_p = self.players[pid]
            new_p, actual = player_ops.repay(old_p, action.amount)
            self.players[pid] = new_p
            self.logger.log("REPAYMENT", round_num, "negotiation", data={
                "player_id": pid, "requested": action.amount, "amount": actual, "turn": turn,
                # 内訳（3%優先充当、§3.5）。test_invariants.py の借金簿記再構成に使う。
                "from_post": old_p.debt_post - new_p.debt_post,
                "from_pre": old_p.debt_pre - new_p.debt_pre,
            })

    # ------------------------------------------------------------------
    # Phase 3: Commit（§7手順3/§4.1/§4.4）
    # ------------------------------------------------------------------

    def _phase_commit(self, round_num: int) -> dict[str, Vote]:
        """
        全員がYESかNOを秘密提出し、参加費を徴収する（§4.1）。

        無効な出力は1回だけ再試行し、それでも駄目ならシステムが代行する
        （§4.4）。投票は全員から集め切ってから Settlement の Reveal で
        公開する（締切後に変更不可、他人に見えない）。
        """
        votes: dict[str, Vote] = {}
        auto_ids: list[str] = []

        for pid in sorted(self.players):
            p = self.players[pid]
            agent = self.agents[pid]
            visible_state = self._build_visible_state(round_num, for_player_id=pid)

            vote: Vote | None = None
            for _attempt in range(2):  # 本番1回＋再試行1回（§4.4）
                try:
                    candidate = agent.commit(p, round_num, visible_state)
                except Exception:
                    candidate = None
                if isinstance(candidate, Vote):
                    vote = candidate
                    break

            if vote is None:
                vote = decide_auto_vote(self.rng, type_b_constraint=None)
                auto_ids.append(pid)
                self.logger.log("AUTO_COMMIT", round_num, "commit", data={
                    "player_id": pid, "vote": vote.value,
                })

            votes[pid] = vote

            # 参加費の徴収（§4.1: Commit時に徴収。§3.4の例外で上限無視の貸付）
            new_p, paid, borrowed, _shortfall = player_ops.pay_or_borrow(
                p, self.config.entry_fee, self.config, cap_exempt=True,
            )
            self.players[pid] = new_p
            self.logger.log("ENTRY_FEE_COLLECTED", round_num, "commit", data={
                "player_id": pid, "paid": paid, "borrowed": borrowed,
            })
            self.logger.log("COMMIT", round_num, "commit", data={
                "player_id": pid, "vote": vote.value,
            })

        self._current_auto_pids = set(auto_ids)
        return votes

    # ------------------------------------------------------------------
    # Phase 4: Settlement（§7.1）
    # ------------------------------------------------------------------

    def _phase_settlement(self, round_num: int, votes: dict[str, Vote], *, is_final: bool) -> None:
        updated, outcome = execute_settlement(
            self.players, votes, self.config, self.carryover, round_num, self.logger,
            is_final_round=is_final,
        )
        self.players = updated
        self.carryover = outcome.carryover_after
        self.total_destroyed_carryover += outcome.destroyed_carryover
        self.total_forfeited_remainder += outcome.forfeited_remainder

        self._last_round_summary = RoundSummary(
            round_num=round_num,
            votes=votes,
            auto_commit_ids=sorted(self._current_auto_pids),
            minority_outcome=outcome,
            interest_total=0,
        )

    # ------------------------------------------------------------------
    # Phase 5: Finance（§7手順5/§3.3）
    # ------------------------------------------------------------------

    def _phase_finance(self, round_num: int) -> None:
        updated, interest_total = execute_finance(self.players, round_num, self.config, self.logger)
        self.players = updated
        self.total_interest += interest_total

        assert self._last_round_summary is not None
        summary = self._last_round_summary.model_copy(update={"interest_total": interest_total})

        if round_num in self.config.rank_public_rounds:
            ranks = player_ops.assets_ranking(self.players.values())
            public_ranks = {pid: r.rank for pid, r in ranks.items()}
            summary = summary.model_copy(update={"public_ranks": public_ranks})
            self._public_ranks_history[round_num] = public_ranks
            self.logger.log("RANK_PUBLISHED", round_num, "finance", data={
                "ranks": dict(sorted(public_ranks.items())),
            })

        self.round_summaries.append(summary)
        self._last_round_summary = summary

    # ------------------------------------------------------------------
    # 終了処理
    # ------------------------------------------------------------------

    def _finalize(self) -> GameResult:
        final_assets = {pid: p.net_assets for pid, p in self.players.items()}
        ranks = player_ops.assets_ranking(self.players.values())
        final_ranks = {pid: r.rank for pid, r in ranks.items()}

        self.logger.log("GAME_END", self.config.num_rounds, "finance", data={
            "final_assets": dict(sorted(final_assets.items())),
            "final_ranks": dict(sorted(final_ranks.items())),
        })

        return GameResult(
            seed=self.seed,
            final_players=dict(self.players),
            final_assets=final_assets,
            final_ranks=final_ranks,
            round_summaries=list(self.round_summaries),
            total_interest=self.total_interest,
            total_destroyed_carryover=self.total_destroyed_carryover,
            total_forfeited_remainder=self.total_forfeited_remainder,
        )

    # ------------------------------------------------------------------
    # 公開情報の構築（§8）
    # ------------------------------------------------------------------

    def _visible_messages(self, for_player_id: str) -> list[dict[str, Any]]:
        """
        当ラウンドのメッセージのうち、for_player_id に見える分だけを返す
        （§8: 全体発言は公開、DMは当事者のみ）
        """
        visible = []
        for m in self._round_messages:
            if m["type"] == "broadcast":
                visible.append(m)
            elif m["type"] == "dm" and for_player_id in (m["from"], m["to"]):
                visible.append(m)
        return visible

    def _build_visible_state(self, round_num: int, for_player_id: str | None = None) -> dict[str, Any]:
        """
        エージェントに渡す公開情報の辞書を構築する（§8）

        秘匿情報（現金・借金残高・残り借入枠・他人の順位・DM・締切前の投票先）
        は for_player_id 本人の分のみ含め、他プレイヤーには一切渡さない。
        """
        # 直近に決着したラウンドの結果（Negotiation/Commit中なら前ラウンド分、
        # reflect()呼び出し中なら今ラウンド分を指す。常に「最後に決着した
        # ラウンド」を指すので round_num による絞り込みは不要）。
        last_round = self._last_round_summary

        state: dict[str, Any] = {
            "round_num": round_num,
            "question": self.questions[round_num - 1],
            "carryover": self.carryover,
            "initial_loans": {pid: p.initial_loan for pid, p in sorted(self.players.items())},
            "public_ranks_history": dict(self._public_ranks_history),
            "last_round_result": None if last_round is None else {
                "round_num": last_round.round_num,
                "votes": {pid: v.value for pid, v in sorted(last_round.votes.items())},
                "minority_side": (
                    last_round.minority_outcome.minority_side.value
                    if last_round.minority_outcome.minority_side else None
                ),
                "minority_ids": last_round.minority_outcome.minority_ids,
                "payout_per_minority": last_round.minority_outcome.payout_per_minority,
                "carryover_after": last_round.minority_outcome.carryover_after,
                "auto_commit_ids": last_round.auto_commit_ids,
            },
        }

        if for_player_id is not None:
            me = self.players.get(for_player_id)
            state["messages"] = self._visible_messages(for_player_id)
            if me is not None:
                interest_pre_forecast = -(
                    -me.debt_pre * self.config.interest_rate_pre_num // self.config.interest_rate_pre_den
                )
                interest_post_forecast = -(
                    -me.debt_post * self.config.interest_rate_post_num // self.config.interest_rate_post_den
                )
                my_rank = player_ops.assets_ranking(self.players.values())[for_player_id]
                state["my_finance"] = {
                    "cash": me.cash,
                    "debt_pre": me.debt_pre,
                    "debt_post": me.debt_post,
                    "total_debt": me.total_debt,
                    "remaining_credit": player_ops.remaining_credit(me, self.config),
                    "interest_forecast": interest_pre_forecast + interest_post_forecast,
                }
                state["my_rank"] = {
                    "rank": my_rank.rank, "tied": my_rank.tied, "n_players": my_rank.n_players,
                }

        return state
