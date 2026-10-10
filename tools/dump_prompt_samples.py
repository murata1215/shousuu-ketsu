"""
文面の見本を作るコマンド（サイクル4.2b B9）

AIは一切呼ばない。固定シードで動かした試合の実際の`visible_state`から、
`llm/prompt_builder.py`の各build_*関数で文面を組み立て、
`doc/analysis/prompt_samples_v0_4.md`へ保存する。

手で書いた文面は貼らない（CLAUDE.md方針）。本番コードがテストコードを
逆輸入しないよう（CLAUDE.md過去の落とし穴⑧）、`tests/helpers.py`は
importせず、台本どおりに動く最小限のエージェントをこのファイル内だけに
用意する。

見本7つ:
  1. ルール全文（システムプロンプト）
  2. 借入額を聞く文面
  3. R1V1・1巡目の交渉
  4. R1V2 の交渉（退場中のプレイヤー。V1の会話・投票結果・成立本数の公示・
     不成立の理由が入った状態）
  5. R1V2 の投票（Commit）
  6. R1 の終わりの振り返り
  7. R3V1 の交渉（全員の順位が公開された後）

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
        self.reflect_snapshots: dict[int, dict[str, Any]] = {}

    def choose_loan(self, config: GameConfig) -> int:
        return self.inner.choose_loan(config)

    def negotiate(self, player_state, round_num, vote_num, turn, visible_state) -> Action:
        self.negotiate_snapshots[(round_num, vote_num, turn)] = copy.deepcopy(visible_state)
        return self.inner.negotiate(player_state, round_num, vote_num, turn, visible_state)

    def commit(self, player_state, round_num, vote_num, visible_state) -> Vote:
        self.commit_snapshots[(round_num, vote_num)] = copy.deepcopy(visible_state)
        return self.inner.commit(player_state, round_num, vote_num, visible_state)

    def reflect(self, player_state, round_num, visible_state) -> None:
        self.reflect_snapshots[round_num] = copy.deepcopy(visible_state)
        return self.inner.reflect(player_state, round_num, visible_state)


def _player(pid: str) -> PlayerState:
    return PlayerState(player_id=pid, cash=0, initial_loan=0)


def _build_fixture() -> tuple[Game, dict[str, _Recorder], GameConfig]:
    """
    4ラウンドぶんの筋書きを持つ固定シードの試合を組み立てる（AIは呼ばない）。

    R1V1: YES(minority,5)=P01,P03,P05,P07,P09 残る / NO(majority,7)=退場
    R1V2: YES(minority,2)=P01,P07 残る・勝ち残り / NO(3)=P03,P05,P09 退場
      - P01がP07へ型A契約（R1終了時200,000円）を提案・署名（V1）
      - P07がV1の1巡目に全体発言
      - P02（V1で退場済み）がV2の1巡目に手持ちを超える送金を試みて不成立
    R2V1: NO(minority,2)=P01,P07 残る・勝ち残り / YES(10)=退場
      - R2終了後に全員の順位が公開される（config.rank_public_rounds=(2,)）
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
    negotiate_actions["P02"][(1, 2, 1)] = TransferAction(player_id="P02", to="P08", amount=50_000_000)

    sign_proposer_at: dict[str, dict[tuple[int, int, int], str]] = {pid: {} for pid in all_ids}
    sign_proposer_at["P07"][(1, 1, 2)] = "P01"

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

    samples: list[tuple[str, str]] = [
        ("1. ルール全文（システムプロンプト）", build_system_prompt("P01", config)),
        ("2. 借入額を聞く文面", build_loan_prompt(config)),
        (
            "3. R1V1・1巡目の交渉",
            build_negotiation_prompt(_player("P01"), 1, 1, 1, p01_neg[(1, 1, 1)], config),
        ),
        (
            "4. R1V2 の交渉（退場中のプレイヤー。V1の会話・投票結果・成立本数の公示・"
            "不成立の理由が入った状態）",
            build_negotiation_prompt(_player("P02"), 1, 2, 2, p02_neg[(1, 2, 2)], config),
        ),
        (
            "5. R1V2 の投票（Commit）",
            build_commit_prompt(_player("P01"), 1, 2, p01_commit[(1, 2)], config),
        ),
        (
            "6. R1 の終わりの振り返り",
            build_reflection_prompt(_player("P01"), 1, p01_reflect[1], config),
        ),
        (
            "7. R3V1 の交渉（全員の順位が公開された後）",
            build_negotiation_prompt(_player("P01"), 3, 1, 1, p01_neg[(3, 1, 1)], config),
        ),
    ]

    lines = [
        "# プロンプト文面の見本（v0.4、サイクル4.2b）",
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
    print(f"見本7つを {OUT_PATH} へ保存しました。")


if __name__ == "__main__":
    main()
