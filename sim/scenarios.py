"""
シナリオ定義モジュール（サイクル1.2、新規実装）

S1〜S6の組み合わせごとに、12人分の RecordingAgent(Bot, label) を構築する。
Bot固有の乱数シードは `seed * 100 + i`（i=プレイヤー番号1〜12）から
導出する（同一シードなら毎回同じBot内部状態で再現する）。

判断した点（計画§6）:
- S3/S4のペアは (P01,P02)(P03,P04)(P05,P06)(P07,P08)(P09,P10)(P11,P12) の
  固定6組を先頭からk組使う。各組はID小さい方が提案者・大きい方が署名者
  （bots/pair_bot.py参照）。
- S4は各組の「ID小さい方」を裏切りに置き換える（判断12、どちらを裏切りに
  するかは任意の選択だが固定して再現性を保つ）。
"""

from engine.config import GameConfig
from sim.recorder import RecordingAgent

from bots.always_no_bot import AlwaysNoBot
from bots.always_yes_bot import AlwaysYesBot
from bots.follow_bot import FollowMajorityBot, FollowMinorityBot
from bots.loan_bot import LoanMaxHoldBot, LoanMaxRepayBot, LoanMinBot
from bots.pair_bot import BetrayerPairBot, PairSplitBot
from bots.random_vote_bot import RandomVoteBot
from tests.helpers import RandomContractAgent

PLAYER_IDS: list[str] = [f"P{i:02d}" for i in range(1, 13)]

# S3/S4のペア割り（ID小さい方, ID大きい方）の固定6組。k組採用時は先頭からk組使う
PAIRS: list[tuple[str, str]] = [
    ("P01", "P02"), ("P03", "P04"), ("P05", "P06"),
    ("P07", "P08"), ("P09", "P10"), ("P11", "P12"),
]

SCENARIO_KEYS: list[str] = ["S1", "S2", "S3k1", "S3k3", "S3k6", "S4", "S5", "S6"]


def _bot_seed(seed: int, pid: str) -> int:
    """プレイヤーIDからBot固有シードを導出する（seed*100+プレイヤー番号）"""
    return seed * 100 + int(pid[1:])


def _s1_all_random(seed: int) -> dict[str, RecordingAgent]:
    return {
        pid: RecordingAgent(RandomVoteBot(seed=_bot_seed(seed, pid)), "Random")
        for pid in PLAYER_IDS
    }


def _s2_mixed_no_contracts(seed: int) -> dict[str, RecordingAgent]:
    assignment = (
        ["Random"] * 4 + ["AlwaysYes"] * 2 + ["AlwaysNo"] * 2
        + ["FollowMinority"] * 2 + ["FollowMajority"] * 2
    )
    cls_by_label = {
        "Random": RandomVoteBot, "AlwaysYes": AlwaysYesBot, "AlwaysNo": AlwaysNoBot,
        "FollowMinority": FollowMinorityBot, "FollowMajority": FollowMajorityBot,
    }
    agents: dict[str, RecordingAgent] = {}
    for pid, label in zip(PLAYER_IDS, assignment):
        bot = cls_by_label[label](seed=_bot_seed(seed, pid))
        agents[pid] = RecordingAgent(bot, label)
    return agents


def _s3_pairs(seed: int, k: int) -> dict[str, RecordingAgent]:
    paired_ids = {pid for pair in PAIRS[:k] for pid in pair}
    agents: dict[str, RecordingAgent] = {}
    for small, large in PAIRS[:k]:
        agents[small] = RecordingAgent(
            PairSplitBot(small, large, seed=_bot_seed(seed, small)), "PairSplit",
        )
        agents[large] = RecordingAgent(
            PairSplitBot(large, small, seed=_bot_seed(seed, large)), "PairSplit",
        )
    for pid in PLAYER_IDS:
        if pid not in paired_ids:
            agents[pid] = RecordingAgent(RandomVoteBot(seed=_bot_seed(seed, pid)), "Random")
    return agents


def _s4_betrayal_pairs(seed: int) -> dict[str, RecordingAgent]:
    """
    S3(k=3)のペアのうち、各組の片方（組の中でIDが小さい方）を
    「裏切り」（BetrayerPairBot）に置き換える
    """
    k = 3
    paired_ids = {pid for pair in PAIRS[:k] for pid in pair}
    agents: dict[str, RecordingAgent] = {}
    for small, large in PAIRS[:k]:
        agents[small] = RecordingAgent(
            BetrayerPairBot(small, large, seed=_bot_seed(seed, small)), "BetrayerPair",
        )
        agents[large] = RecordingAgent(
            PairSplitBot(large, small, seed=_bot_seed(seed, large)), "PairSplit",
        )
    for pid in PLAYER_IDS:
        if pid not in paired_ids:
            agents[pid] = RecordingAgent(RandomVoteBot(seed=_bot_seed(seed, pid)), "Random")
    return agents


def _s5_loan_types(seed: int) -> dict[str, RecordingAgent]:
    assignment = ["LoanMin"] * 4 + ["LoanMaxRepay"] * 4 + ["LoanMaxHold"] * 4
    cls_by_label = {
        "LoanMin": LoanMinBot, "LoanMaxRepay": LoanMaxRepayBot, "LoanMaxHold": LoanMaxHoldBot,
    }
    agents: dict[str, RecordingAgent] = {}
    for pid, label in zip(PLAYER_IDS, assignment):
        bot = cls_by_label[label](seed=_bot_seed(seed, pid))
        agents[pid] = RecordingAgent(bot, label)
    return agents


def _s6_random_contracts(seed: int, config: GameConfig) -> dict[str, RecordingAgent]:
    agents: dict[str, RecordingAgent] = {}
    for pid in PLAYER_IDS:
        bot = RandomContractAgent(pid, seed=_bot_seed(seed, pid), num_rounds=config.num_rounds)
        agents[pid] = RecordingAgent(bot, "RandomContract")
    return agents


def build_agents(scenario_key: str, seed: int, config: GameConfig) -> dict[str, RecordingAgent]:
    """
    シナリオキーとシードから、12人分のRecordingAgentを構築する

    Args:
        scenario_key: "S1"/"S2"/"S3k1"/"S3k3"/"S3k6"/"S4"/"S5"/"S6"
        seed: 試合シード（Bot固有シードはここから導出。GameRngのseedとは別軸）
        config: ゲーム設定（S6のnum_rounds参照用）

    Returns:
        player_id -> RecordingAgent の辞書（ちょうど12人）
    """
    if scenario_key == "S1":
        return _s1_all_random(seed)
    if scenario_key == "S2":
        return _s2_mixed_no_contracts(seed)
    if scenario_key == "S3k1":
        return _s3_pairs(seed, 1)
    if scenario_key == "S3k3":
        return _s3_pairs(seed, 3)
    if scenario_key == "S3k6":
        return _s3_pairs(seed, 6)
    if scenario_key == "S4":
        return _s4_betrayal_pairs(seed)
    if scenario_key == "S5":
        return _s5_loan_types(seed)
    if scenario_key == "S6":
        return _s6_random_contracts(seed, config)
    raise ValueError(f"Unknown scenario key: {scenario_key!r}")
