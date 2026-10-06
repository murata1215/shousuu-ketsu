"""
ルールベースBotパッケージ

LLMを使わない決定論的Botを提供する。全BotはPlayerAgentを継承する。
サイクル1.0は動作確認用の2種（無作為投票／常にYES）のみだったが、
サイクル1.2（§12.1のBot検証）で常にNO・前回少数派/多数派に乗る・
借入3通りを追加した。ペア割り・裏切り（PairSplitBot/BetrayerPairBot、
bots/pair_bot.py）は相手IDをコンストラクタで固定する必要があるため、
BOT_REGISTRY（引数なしでseedだけ渡して作れるBot）には登録せず、
sim/scenarios.py がペアごとに直接インスタンス化する。
"""

from bots.always_no_bot import AlwaysNoBot
from bots.always_yes_bot import AlwaysYesBot
from bots.follow_bot import FollowMajorityBot, FollowMinorityBot
from bots.loan_bot import LoanMaxHoldBot, LoanMaxRepayBot, LoanMinBot
from bots.random_vote_bot import RandomVoteBot

# Bot名 → クラスのレジストリ（simulate.py/dry_run.pyのrosterオプション用）
# 既存テスト（tests/test_game_loop.py, scripts/dry_run.py --bots）が
# DEFAULT_ROSTER=["Random","AlwaysYes"]を前提にしているため、新規Botを
# 追加してもDEFAULT_ROSTERはそのまま維持する。
BOT_REGISTRY: dict[str, type] = {
    "Random": RandomVoteBot,
    "AlwaysYes": AlwaysYesBot,
    "AlwaysNo": AlwaysNoBot,
    "FollowMinority": FollowMinorityBot,
    "FollowMajority": FollowMajorityBot,
    "LoanMin": LoanMinBot,
    "LoanMaxRepay": LoanMaxRepayBot,
    "LoanMaxHold": LoanMaxHoldBot,
}

# デフォルトロスター（既存の挙動を変えないため従来の2種のまま固定）
DEFAULT_ROSTER: list[str] = ["Random", "AlwaysYes"]
