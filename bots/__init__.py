"""
ルールベースBotパッケージ

LLMを使わない決定論的Botを提供する。全BotはPlayerAgentを継承する。
本サイクル（1.0）は動作確認用の2種のみ（無作為投票／常にYES）。
残り4種（§12.1: 2人で票を割る／前回の少数派側に入れる／契約を破る／
金で票を買う）はサイクル1.4で追加する。
"""

from bots.always_yes_bot import AlwaysYesBot
from bots.random_vote_bot import RandomVoteBot

# Bot名 → クラスのレジストリ（simulate.py/dry_run.pyのrosterオプション用）
BOT_REGISTRY: dict[str, type] = {
    "Random": RandomVoteBot,
    "AlwaysYes": AlwaysYesBot,
}

# デフォルトロスター
DEFAULT_ROSTER: list[str] = list(BOT_REGISTRY.keys())
