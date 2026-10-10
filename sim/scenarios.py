"""
シナリオ定義モジュール（v0.4対応、サイクル4.1で全面作り直し）

v0.3のS1〜S6（ラウンド=投票1回・12ラウンド制）は仕様書v0.4の勝ち抜き制
（L12R4V6）で意味が失われたため削除し、サイクル4.1の計画（V1〜V10・
設定値を変えた比較）に合わせて作り直した（CLAUDE.md落とし穴⑨）。

各シナリオは `build_agents(scenario_key, seed) -> (agents, config)` から
呼ばれる専用のビルダー関数を持つ。Bot固有の乱数シードは
`seed * 100 + i`（i=プレイヤー番号1〜12、v0.3からの既存の流儀を継続）。

設定値の上書き（型Bの違約金・開始後の利率・打ち切りまでの連続回数）は
SCENARIOSテーブルの`config_overrides`にまとめ、CLIフラグを増やさずに
シナリオキーだけで切り替えられるようにした。打ち切りまでの連続回数を
変える条件は、構造的な1ラウンドの最大投票数（= 2 × 連続回数。決着は
最大2回なので）も一緒に変える必要がある（engine/game.py:
vote_num > max_votes_per_round でAssertionErrorになるため）。
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from bots.group_bot import GroupSplitBot, MultiGroupHubBot, SitInBot, TypeBPactGroupBot
from bots.loan_bot import LoanFixedBot
from bots.oversell_bot import OversellBot, SignerBot
from bots.random_vote_bot import RandomVoteBot
from engine.config import GameConfig
from sim.recorder import RecordingAgent

PLAYER_IDS: list[str] = [f"P{i:02d}" for i in range(1, 13)]


def _bot_seed(seed: int, pid: str) -> int:
    """プレイヤーIDからBot固有シードを導出する（seed*100+プレイヤー番号）"""
    return seed * 100 + int(pid[1:])


def _random_fill(seed: int, used: set[str]) -> dict[str, RecordingAgent]:
    """グループに入っていない残りのプレイヤーを無作為Botで埋める"""
    return {
        pid: RecordingAgent(RandomVoteBot(seed=_bot_seed(seed, pid)), "Random")
        for pid in PLAYER_IDS if pid not in used
    }


def _group_agents(
    seed: int, members: list[str], bot_cls: type = GroupSplitBot, **kwargs: Any,
) -> dict[str, RecordingAgent]:
    label = kwargs.pop("label", bot_cls.__name__.replace("Bot", ""))
    return {
        pid: RecordingAgent(bot_cls(pid, members, seed=_bot_seed(seed, pid), **kwargs), label)
        for pid in members
    }


# ---------------------------------------------------------------------------
# V1: 12人とも無作為
# ---------------------------------------------------------------------------

def _v1(seed: int) -> dict[str, RecordingAgent]:
    return _random_fill(seed, used=set())


# ---------------------------------------------------------------------------
# V2: 4人組1つ＋無作為8人
# ---------------------------------------------------------------------------

GROUP_A = ["P01", "P02", "P03", "P04"]
GROUP_B = ["P05", "P06", "P07", "P08"]
GROUP_C = ["P09", "P10", "P11", "P12"]


def _v2(seed: int) -> dict[str, RecordingAgent]:
    agents = _group_agents(seed, GROUP_A, GroupSplitBot, label="Group")
    agents.update(_random_fill(seed, used=set(GROUP_A)))
    return agents


# ---------------------------------------------------------------------------
# V3: 組の人数を2・3・5・6人に変える（先頭からその人数、残りは無作為）
# ---------------------------------------------------------------------------

def _v3(seed: int, k: int) -> dict[str, RecordingAgent]:
    members = PLAYER_IDS[:k]
    agents = _group_agents(seed, members, GroupSplitBot, label="Group")
    agents.update(_random_fill(seed, used=set(members)))
    return agents


# ---------------------------------------------------------------------------
# V4: 4人組2つ＋無作為4人
# ---------------------------------------------------------------------------

def _v4(seed: int) -> dict[str, RecordingAgent]:
    agents = _group_agents(seed, GROUP_A, GroupSplitBot, label="GroupA")
    agents.update(_group_agents(seed, GROUP_B, GroupSplitBot, label="GroupB"))
    agents.update(_random_fill(seed, used=set(GROUP_A) | set(GROUP_B)))
    return agents


# ---------------------------------------------------------------------------
# V5: 4人組3つ（全員が組む）
# ---------------------------------------------------------------------------

def _v5(seed: int) -> dict[str, RecordingAgent]:
    agents = _group_agents(seed, GROUP_A, GroupSplitBot, label="GroupA")
    agents.update(_group_agents(seed, GROUP_B, GroupSplitBot, label="GroupB"))
    agents.update(_group_agents(seed, GROUP_C, GroupSplitBot, label="GroupC"))
    return agents


# ---------------------------------------------------------------------------
# V6: 居座り（V2と同じ組＋外8人が、残り4人で組2人+外2人になったら割れる）
# ---------------------------------------------------------------------------

def _v6(seed: int) -> dict[str, RecordingAgent]:
    agents = _group_agents(seed, GROUP_A, GroupSplitBot, label="Group")
    outsiders = [pid for pid in PLAYER_IDS if pid not in GROUP_A]
    for pid in outsiders:
        agents[pid] = RecordingAgent(
            SitInBot(pid, group_members=GROUP_A, seed=_bot_seed(seed, pid)), "SitIn",
        )
    return agents


# ---------------------------------------------------------------------------
# V7: 掛け持ち（P01が3つの4人組に入る。本人＋3人×3組＝10人、外2人）
# ---------------------------------------------------------------------------

HUB_ID = "P01"
HUB_GROUPS: list[list[str]] = [
    ["P02", "P03", "P04"], ["P05", "P06", "P07"], ["P08", "P09", "P10"],
]
HUB_OUTSIDERS = ["P11", "P12"]


def _v7(seed: int) -> dict[str, RecordingAgent]:
    agents: dict[str, RecordingAgent] = {
        HUB_ID: RecordingAgent(
            MultiGroupHubBot(HUB_ID, HUB_GROUPS, seed=_bot_seed(seed, HUB_ID)), "Hub",
        ),
    }
    for idx, group in enumerate(HUB_GROUPS, start=1):
        agents.update(_group_agents(seed, group, GroupSplitBot, label=f"G{idx}"))
    for pid in HUB_OUTSIDERS:
        agents[pid] = RecordingAgent(RandomVoteBot(seed=_bot_seed(seed, pid)), "Random")
    return agents


# ---------------------------------------------------------------------------
# V8: 型Bで票を縛り合う4人組の中で、1人（P01）が違約金を払って約束を破る
# ---------------------------------------------------------------------------

def _v8(seed: int, *, breaks: bool) -> dict[str, RecordingAgent]:
    breaker_id = GROUP_A[0]  # ID最小=P01
    agents: dict[str, RecordingAgent] = {}
    for pid in GROUP_A:
        bot_breaks = breaks and pid == breaker_id
        label = "TypeBBreaker" if bot_breaks else "TypeBPact"
        agents[pid] = RecordingAgent(
            TypeBPactGroupBot(pid, GROUP_A, seed=_bot_seed(seed, pid), breaks=bot_breaks), label,
        )
    agents.update(_random_fill(seed, used=set(GROUP_A)))
    return agents


# ---------------------------------------------------------------------------
# V9: 借入額120万・400万・1000万を混ぜる（投票は無作為）
# ---------------------------------------------------------------------------

LOAN_AMOUNTS: list[tuple[str, int]] = [
    ("Loan120man", 1_200_000), ("Loan400man", 4_000_000), ("Loan1000man", 10_000_000),
]


def _v9(seed: int) -> dict[str, RecordingAgent]:
    agents: dict[str, RecordingAgent] = {}
    idx = 0
    for label, loan in LOAN_AMOUNTS:
        for _ in range(4):
            pid = PLAYER_IDS[idx]
            idx += 1
            agents[pid] = RecordingAgent(
                LoanFixedBot(loan=loan, seed=_bot_seed(seed, pid), bot_type=label), label,
            )
    return agents


# ---------------------------------------------------------------------------
# V10: 割合の重ね売り（P01が3本の契約で、それぞれ勝ち残ったら50%を払う約束）
# ---------------------------------------------------------------------------

def _v10(seed: int) -> dict[str, RecordingAgent]:
    proposer, signers = "P01", ["P02", "P03", "P04"]
    agents: dict[str, RecordingAgent] = {
        proposer: RecordingAgent(
            OversellBot(proposer, signers=signers, seed=_bot_seed(seed, proposer)), "Oversell",
        ),
    }
    for pid in signers:
        agents[pid] = RecordingAgent(
            SignerBot(pid, proposer_id=proposer, seed=_bot_seed(seed, pid)), "Signer",
        )
    agents.update(_random_fill(seed, used={proposer, *signers}))
    return agents


@dataclass(frozen=True)
class Scenario:
    key: str
    build: Callable[[int], dict[str, RecordingAgent]]
    config_overrides: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def _ties_overrides(max_consecutive_ties: int) -> dict[str, Any]:
    """
    打ち切りまでのやり直しの連続回数を変える条件向けの設定値

    構造的な1ラウンドの最大投票数は「2 × 連続回数」（決着で退場できるのは
    最大2回、12人設定では7対5→5人残り→3対2のように毎回決着するため）。
    既定の3→6はこの関係のとおり（仕様書v0.4.1 §10と一致）。
    """
    return {
        "max_consecutive_ties": max_consecutive_ties,
        "max_votes_per_round": max_consecutive_ties * 2,
    }


SCENARIOS: dict[str, Scenario] = {
    "V1": Scenario("V1", _v1, note="12人とも無作為（基準）"),
    "V2": Scenario("V2", _v2, note="4人組1つ＋無作為8人"),
    "V3g2": Scenario("V3g2", lambda seed: _v3(seed, 2), note="組の人数2人"),
    "V3g3": Scenario("V3g3", lambda seed: _v3(seed, 3), note="組の人数3人"),
    "V3g5": Scenario("V3g5", lambda seed: _v3(seed, 5), note="組の人数5人"),
    "V3g6": Scenario("V3g6", lambda seed: _v3(seed, 6), note="組の人数6人"),
    "V4": Scenario("V4", _v4, note="4人組2つ＋無作為4人"),
    "V5": Scenario("V5", _v5, note="4人組3つ（全員が組む）"),
    "V6": Scenario("V6", _v6, note="居座り"),
    "V7": Scenario("V7", _v7, note="掛け持ち"),
    "V8keep_pen100": Scenario(
        "V8keep_pen100", lambda seed: _v8(seed, breaks=False), note="型B遵守・違約金100万（既定）",
    ),
    "V8keep_pen300": Scenario(
        "V8keep_pen300", lambda seed: _v8(seed, breaks=False),
        config_overrides={"penalty_amount": 3_000_000}, note="型B遵守・違約金300万",
    ),
    "V8keep_pen500": Scenario(
        "V8keep_pen500", lambda seed: _v8(seed, breaks=False),
        config_overrides={"penalty_amount": 5_000_000}, note="型B遵守・違約金500万",
    ),
    "V8break_pen100": Scenario(
        "V8break_pen100", lambda seed: _v8(seed, breaks=True), note="P01が破る・違約金100万（既定）",
    ),
    "V8break_pen300": Scenario(
        "V8break_pen300", lambda seed: _v8(seed, breaks=True),
        config_overrides={"penalty_amount": 3_000_000}, note="P01が破る・違約金300万",
    ),
    "V8break_pen500": Scenario(
        "V8break_pen500", lambda seed: _v8(seed, breaks=True),
        config_overrides={"penalty_amount": 5_000_000}, note="P01が破る・違約金500万",
    ),
    "V9": Scenario("V9", _v9, note="借入120万/400万/1000万・利率15%（既定）"),
    "V9_post10": Scenario(
        "V9_post10", _v9, config_overrides={"interest_rate_post_num": 10}, note="開始後の利率10%",
    ),
    "V10": Scenario("V10", _v10, note="割合の重ね売り"),
    "V1_ties2": Scenario("V1_ties2", _v1, config_overrides=_ties_overrides(2), note="連続回数2回"),
    "V1_ties5": Scenario("V1_ties5", _v1, config_overrides=_ties_overrides(5), note="連続回数5回"),
    "V5_ties2": Scenario("V5_ties2", _v5, config_overrides=_ties_overrides(2), note="連続回数2回"),
    "V5_ties5": Scenario("V5_ties5", _v5, config_overrides=_ties_overrides(5), note="連続回数5回"),
    "V6_ties2": Scenario("V6_ties2", _v6, config_overrides=_ties_overrides(2), note="連続回数2回"),
    "V6_ties5": Scenario("V6_ties5", _v6, config_overrides=_ties_overrides(5), note="連続回数5回"),
}

SCENARIO_KEYS: list[str] = list(SCENARIOS.keys())


def config_for(scenario_key: str) -> GameConfig:
    """シナリオキーから、設定値の上書きを反映したGameConfigを返す"""
    scenario = SCENARIOS[scenario_key]
    return GameConfig.default_12().model_copy(update=scenario.config_overrides)


def build_agents(scenario_key: str, seed: int) -> tuple[dict[str, RecordingAgent], GameConfig]:
    """
    シナリオキーとシードから、12人分のRecordingAgentと設定を構築する

    Args:
        scenario_key: SCENARIO_KEYS のいずれか
        seed: 試合シード（Bot固有シードはここから導出。GameRngのseedとは別軸）

    Returns:
        (player_id -> RecordingAgent の辞書（ちょうど12人）, GameConfig)
    """
    if scenario_key not in SCENARIOS:
        raise ValueError(f"Unknown scenario key: {scenario_key!r} (valid: {SCENARIO_KEYS})")
    scenario = SCENARIOS[scenario_key]
    agents = scenario.build(seed)
    if set(agents) != set(PLAYER_IDS):
        raise AssertionError(f"{scenario_key}: agents must cover exactly {PLAYER_IDS}, got {sorted(agents)}")
    return agents, config_for(scenario_key)
