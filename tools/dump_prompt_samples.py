"""
文面の見本を作るコマンド（サイクル4.2b B9、サイクル4.2cで4つ追加、
サイクル4.2dで1つ追加）

AIは一切呼ばない。固定シードで動かした試合の実際の`visible_state`から、
`llm/prompt_builder.py`の各build_*関数で文面を組み立て、
`doc/analysis/prompt_samples_v0_4.md`へ保存する。

手で書いた文面は貼らない（CLAUDE.md方針）。本番コードがテストコードを
逆輸入しないよう（CLAUDE.md過去の落とし穴⑧）、`tests/helpers.py`は
importせず、台本どおりに動く最小限のエージェントをこのファイル内だけに
用意する。

見本12つ:
  1. ルール全文（システムプロンプト）
  2. 借入額を聞く文面
  3. R1V1・1巡目の交渉
  4. R1V2 の交渉（退場中のプレイヤー。V1の会話・投票結果・成立本数の公示・
     不成立の理由が入った状態）
  5. R1V2 の投票（Commit）
  6. R1 の終わりの振り返り
  7. R3V1 の交渉（全員の順位が公開された後）
  8. R1V1 の振り返り（ラウンドがV2へ続く場面）
  9. 続く R1V2 の交渉（V1の会話が入らず、メモが入っている）
  10. R2V1 の交渉（R1で送金を受け取り、割合契約の支払いを受け、型Aを
      払い終えたプレイヤー。前のラウンドで全義務が済んだ契約は出ない）
  11. 上限に届いて古い発言が省かれた、R1V1 終盤の交渉
  12. R1V1・8巡目の交渉（本人が署名済みで相手を待っている提案と、相手が
      提案して本人の署名を待っている提案が、同時にある場面。署名待ちの
      提案が2つの見出しに分かれて出る、サイクル4.2d・§7.5）

使用方法:
    uv run python tools/dump_prompt_samples.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig  # noqa: E402
from engine.events import EventLogger  # noqa: E402
from engine.game import Game  # noqa: E402
from engine.models import (  # noqa: E402
    Action, BroadcastAction, ContractProposeAction, ContractSignAction,
    PassAction, PlayerState, TransferAction, Vote,
)
from engine.negotiation import PlayerAgent  # noqa: E402
from llm.prompt_builder import (  # noqa: E402
    build_commit_prompt, build_loan_prompt, build_negotiation_prompt,
    build_reflection_prompt, build_system_prompt,
)

OUT_PATH = Path("doc/analysis/prompt_samples_v0_4.md")


class _ScriptedAgent(PlayerAgent):
    """
    本ツール専用の最小限の台本エージェント（tests/helpers.pyのテストコードを
    importしない。CLAUDE.md過去の落とし穴⑧）。

    (round_num, vote_num) -> Vote、(round_num, vote_num, turn) -> Action、
    (round_num, vote_num, turn) -> 署名したい提案者player_idを指定できる。
    """

    def __init__(
        self,
        player_id: str,
        votes: dict[tuple[int, int], Vote] | None = None,
        negotiate_actions: dict[tuple[int, int, int], Action] | None = None,
        sign_proposer_at: dict[tuple[int, int, int], str] | None = None,
        loan: int = 1_200_000,
    ) -> None:
        self.player_id = player_id
        self.votes = votes or {}
        self.negotiate_actions = negotiate_actions or {}
        self.sign_proposer_at = sign_proposer_at or {}
        self.loan = loan

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        proposer = self.sign_proposer_at.get((round_num, vote_num, turn))
        if proposer is not None:
            for c in visible_state.get("contracts_pending", []):
                if c["proposer"] == proposer and self.player_id not in c["signed_by"]:
                    return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return self.negotiate_actions.get(
            (round_num, vote_num, turn), PassAction(player_id=self.player_id),
        )

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.votes.get((round_num, vote_num), Vote.YES)


class _Recorder(PlayerAgent):
    """negotiate/commit/reflect呼び出しのたびにvisible_stateのコピーを控える"""

    def __init__(self, inner: PlayerAgent) -> None:
        self.inner = inner
        self.player_id = inner.player_id
        self.negotiate_snapshots: dict[tuple[int, int, int], dict[str, Any]] = {}
        self.commit_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
        self.reflect_snapshots: dict[tuple[int, int], dict[str, Any]] = {}

    def choose_loan(self, config: GameConfig) -> int:
        return self.inner.choose_loan(config)

    def negotiate(self, player_state, round_num, vote_num, turn, visible_state) -> Action:
        self.negotiate_snapshots[(round_num, vote_num, turn)] = copy.deepcopy(visible_state)
        return self.inner.negotiate(player_state, round_num, vote_num, turn, visible_state)

    def commit(self, player_state, round_num, vote_num, visible_state) -> Vote:
        self.commit_snapshots[(round_num, vote_num)] = copy.deepcopy(visible_state)
        return self.inner.commit(player_state, round_num, vote_num, visible_state)

    def reflect(self, player_state, round_num, vote_num, visible_state, *, is_round_end) -> None:
        self.reflect_snapshots[(round_num, vote_num)] = copy.deepcopy(visible_state)
        return self.inner.reflect(
            player_state, round_num, vote_num, visible_state, is_round_end=is_round_end,
        )


def _player(pid: str) -> PlayerState:
    return PlayerState(player_id=pid, cash=0, initial_loan=0)


def _build_fixture() -> tuple[Game, dict[str, _Recorder], GameConfig]:
    """
    4ラウンドぶんの筋書きを持つ固定シードの試合を組み立てる（AIは呼ばない）。

    R1V1: YES(minority,5)=P01,P03,P05,P07,P09 残る / NO(majority,7)=退場
      - P01がP07へ型A契約（R1終了時200,000円）を提案・署名（1巡目・2巡目）
      - P07がV1の1巡目に全体発言
      - P03がP01へ送金30,000円（3巡目）
      - P07がP01へ型C(wins_round・割合10%)契約を提案・署名（3巡目・4巡目）
      - P01がP07へ3種の義務を持つ契約（型A・R1払い150,000円／型B・R1V2は
        YES／型A・R2払い400,000円）を提案・署名（5巡目・6巡目）。見本10
        （R2V1の交渉）で、済んだ義務・型Bを守った義務・これからの義務が
        それぞれ1本以上そろうようにするための契約（B-1）
      - 1〜9巡目は全員が長い全体発言をする（既存の指定が無い巡だけ。
        見本11の「上限に届いて古い発言が省かれる」場面を作るための会話量）
    R1V2: YES(minority,2)=P01,P07 残る・勝ち残り / NO(3)=P03,P05,P09 退場
      - P02（V1で退場済み）がV2の1巡目に手持ちを超える送金を試みて不成立
      - P01はYESに投票（上記契約の型B義務を守る）
    R2V1: NO(minority,2)=P01,P07 残る・勝ち残り / YES(10)=退場
      - R2終了後に全員の順位が公開される（config.rank_public_rounds=(2,)）
      - R1でP01が当事者だった契約のうち、型A(R1終了時200,000円)・型C
        (wins_round割合10%)は両方ともR1の精算で全義務が済んでおり、
        R2V1の時点ではmy_contract_statusに出ない（§11.8 #10）。5巡目・
        6巡目で結んだ3種義務の契約は、型A(R1払い)が済み・型Bが守った済みに
        なった一方、型A(R2払い)がまだ「これから」なので、契約自体は
        R2V1でも表示に残る（Game._contract_status_viewがこれからの義務の
        有無で判定するため）
    R3V1: 任意（サンプル取得後の進行は問わない）
    """
    config = GameConfig.default_12()
    all_ids = [f"P{i:02d}" for i in range(1, 13)]

    r1v1_yes = {"P01", "P03", "P05", "P07", "P09"}
    r1v2_yes = {"P01", "P07"}
    r2v1_no = {"P01", "P07"}

    votes_by_pid: dict[str, dict[tuple[int, int], Vote]] = {pid: {} for pid in all_ids}
    for pid in all_ids:
        votes_by_pid[pid][(1, 1)] = Vote.YES if pid in r1v1_yes else Vote.NO
    for pid in r1v1_yes:
        votes_by_pid[pid][(1, 2)] = Vote.YES if pid in r1v2_yes else Vote.NO
    for pid in all_ids:
        votes_by_pid[pid][(2, 1)] = Vote.NO if pid in r2v1_no else Vote.YES
        votes_by_pid[pid][(3, 1)] = Vote.YES  # 任意（R3以降は見本に使わない）

    negotiate_actions: dict[str, dict[tuple[int, int, int], Action]] = {pid: {} for pid in all_ids}
    negotiate_actions["P01"][(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P07"],
        terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 200_000}}],
    )
    negotiate_actions["P07"][(1, 1, 1)] = BroadcastAction(
        player_id="P07", message="最初はYESでそろえたい。乗る人はDMをくれ。",
    )
    negotiate_actions["P03"][(1, 1, 3)] = TransferAction(player_id="P03", to="P01", amount=30_000)
    negotiate_actions["P07"][(1, 1, 3)] = ContractProposeAction(
        player_id="P07", with_players=["P01"],
        terms=[{"obligor": "P07", "counterparty": "P01", "ob_type": "type_c_conditional",
                "round_num": 1,
                "details": {"share_percent": 10, "condition_type": "wins_round",
                            "condition": {"target_player": "P07"}}}],
    )
    negotiate_actions["P02"][(1, 2, 1)] = TransferAction(player_id="P02", to="P08", amount=50_000_000)

    # 見本12（署名待ちの2つの見出しが同時に出る場面）用に、どちらも最後まで
    # 署名がそろわない（誰にも署名させない）契約を2本つくる。
    # 契約D: P01がP09へ提案（P01は自動で署名済み、P09は署名しない）→
    #   P01視点では「あなたは署名済みで、相手の署名を待っている」側
    negotiate_actions["P01"][(1, 1, 7)] = ContractProposeAction(
        player_id="P01", with_players=["P09"],
        terms=[{"obligor": "P01", "counterparty": "P09", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 100_000}}],
    )
    # 契約E: P03がP01へ提案（P03は自動で署名済み、P01は署名しない）→
    #   P01視点では「あなたの署名を待っている」側
    negotiate_actions["P03"][(1, 1, 1)] = ContractProposeAction(
        player_id="P03", with_players=["P01"],
        terms=[{"obligor": "P03", "counterparty": "P01", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 50_000}}],
    )

    # 見本10（お金の出入りが入ったR2V1の交渉）用に、済んだ義務（型A・R1払い）・
    # 型Bを守った義務（R1V2のYES）・これからの義務（型A・R2払い）が1本ずつ
    # そろう契約を、P01がP07へ提案・署名する（B-1）。
    negotiate_actions["P01"][(1, 1, 5)] = ContractProposeAction(
        player_id="P01", with_players=["P07"],
        terms=[
            {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
             "round_num": 1, "details": {"amount": 150_000}},
            {"obligor": "P01", "counterparty": "P07", "ob_type": "type_b_vote",
             "round_num": 1, "vote_num": 2, "details": {"vote": "YES"}},
            {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
             "round_num": 2, "details": {"amount": 400_000}},
        ],
    )

    # 見本11（上限に届いて古い発言が省かれる場面）用に、R1V1の1〜9巡目は
    # 全員が長めの全体発言をする（既に別の行動が指定されている巡は上書きしない）。
    for turn in range(1, 10):
        for pid in all_ids:
            negotiate_actions[pid].setdefault(
                (1, 1, turn),
                BroadcastAction(
                    player_id=pid,
                    message=f"{pid}の{turn}巡目の発言。" + "あ" * 280,
                ),
            )

    sign_proposer_at: dict[str, dict[tuple[int, int, int], str]] = {pid: {} for pid in all_ids}
    sign_proposer_at["P07"][(1, 1, 2)] = "P01"
    sign_proposer_at["P01"][(1, 1, 4)] = "P07"
    sign_proposer_at["P07"][(1, 1, 6)] = "P01"

    agents: dict[str, PlayerAgent] = {
        pid: _ScriptedAgent(
            pid, votes=votes_by_pid[pid], negotiate_actions=negotiate_actions[pid],
            sign_proposer_at=sign_proposer_at[pid],
        )
        for pid in all_ids
    }
    recorders = {pid: _Recorder(agents[pid]) for pid in ("P01", "P02", "P07")}
    for pid, rec in recorders.items():
        agents[pid] = rec

    game = Game(config=config, agents=agents, seed=42, logger=EventLogger(), stop_after_round=3)
    game.run()
    return game, recorders, config


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n```text\n{body}\n```\n\n"


def main() -> None:
    game, recorders, config = _build_fixture()

    p01_neg = recorders["P01"].negotiate_snapshots
    p01_commit = recorders["P01"].commit_snapshots
    p01_reflect = recorders["P01"].reflect_snapshots
    p02_neg = recorders["P02"].negotiate_snapshots

    # 見本9: R1V1の振り返りで本人が書いたメモ（台本エージェントは実際には
    # メモを書かないため、R1V2の文面を組み立てる入力として明示的に渡す。
    # エンジンのvisible_stateにメモは一切含まれない——メモはLLMAgent側の
    # 状態であり文面層へ引数で渡る、§9.4）。
    sample_memory = (
        "R1V1: P07と組んでYESでそろえ、5人残った（決着）。P07への型A20万の"
        "契約と、P07からの型C割合10%の契約がどちらも成立。V2もP07と組む方針。"
    )

    samples: list[tuple[str, str]] = [
        ("1. ルール全文（システムプロンプト）", build_system_prompt("P01", config)),
        ("2. 借入額を聞く文面", build_loan_prompt(config)),
        (
            "3. R1V1・1巡目の交渉",
            build_negotiation_prompt(_player("P01"), 1, 1, 1, p01_neg[(1, 1, 1)], config).text,
        ),
        (
            "4. R1V2 の交渉（退場中のプレイヤー。V1の会話・投票結果・成立本数の公示・"
            "不成立の理由が入った状態）",
            build_negotiation_prompt(_player("P02"), 1, 2, 2, p02_neg[(1, 2, 2)], config).text,
        ),
        (
            "5. R1V2 の投票（Commit）",
            build_commit_prompt(_player("P01"), 1, 2, p01_commit[(1, 2)], config).text,
        ),
        (
            "6. R1 の終わりの振り返り",
            build_reflection_prompt(
                _player("P01"), 1, 2, p01_reflect[(1, 2)], config, is_round_end=True,
            ).text,
        ),
        (
            "7. R3V1 の交渉（全員の順位が公開された後）",
            build_negotiation_prompt(_player("P01"), 3, 1, 1, p01_neg[(3, 1, 1)], config).text,
        ),
        (
            "8. R1V1 の振り返り（ラウンドがV2へ続く場面）",
            build_reflection_prompt(
                _player("P01"), 1, 1, p01_reflect[(1, 1)], config, is_round_end=False,
            ).text,
        ),
        (
            "9. 続く R1V2 の交渉（V1の会話が入らず、メモが入っている）",
            build_negotiation_prompt(
                _player("P01"), 1, 2, 1, p01_neg[(1, 2, 1)], config, memory=sample_memory,
            ).text,
        ),
        (
            "10. R2V1 の交渉（R1で送金を受け取り、割合契約の支払いを受け、型Aを"
            "払い終えたプレイヤー。前のラウンドで全義務が済んだ契約はここに出ない）",
            build_negotiation_prompt(_player("P01"), 2, 1, 1, p01_neg[(2, 1, 1)], config).text,
        ),
        (
            "11. 上限に届いて古い発言が省かれた、R1V1 終盤の交渉",
            build_negotiation_prompt(_player("P01"), 1, 1, 10, p01_neg[(1, 1, 10)], config).text,
        ),
        (
            "12. R1V1・8巡目の交渉（本人が署名済みで相手を待っている提案と、"
            "相手が提案して本人の署名を待っている提案が、同時にある場面）",
            build_negotiation_prompt(_player("P01"), 1, 1, 8, p01_neg[(1, 1, 8)], config).text,
        ),
    ]

    lines = [
        "# プロンプト文面の見本（v0.4.4、サイクル4.2d）",
        "",
        "`tools/dump_prompt_samples.py`が、固定シードの試合（AIは呼ばない。"
        "台本どおりに動くエージェント）の実際の`visible_state`から"
        "`llm/prompt_builder.py`で組み立てた文面をそのまま保存したもの。"
        "手で書いた文面は含まない。",
        "",
    ]
    for title, body in samples:
        lines.append(_section(title, body))

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"見本{len(samples)}つを {OUT_PATH} へ保存しました。")


if __name__ == "__main__":
    main()
