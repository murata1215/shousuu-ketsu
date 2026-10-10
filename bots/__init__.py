"""
ルールベースBotパッケージ

LLMを使わない決定論的Botを提供する。全BotはPlayerAgentを継承する。
サイクル1.0は動作確認用の2種（無作為投票／常にYES）のみだったが、
サイクル1.2（§12.1のBot検証）で常にNO・前回少数派/多数派に乗る・
借入3通りを追加した。

サイクル4.0（v0.4）で RandomContractBot（bots/random_contract_bot.py）を
追加した。player_id を必須引数に取るため同じ理由でBOT_REGISTRYには
登録せず、scripts/dry_run.py・tests/test_invariants.py が直接
インスタンス化する（v0.3までtests/helpers.pyにあった
RandomContractAgentの後継。本番コードからテストコードへの逆依存を
解消するためbots/へ移した）。

サイクル4.1（v0.4のBot検証）で次の2点を変更した。
- v0.3の「ペア割り」（bots/pair_bot.py、ラウンド丸ごと2人で型B）は
  v0.4のラウンド/投票の二重構造に合わなくなったため削除し、
  bots/group_bot.py（GroupSplitBot/TypeBPactGroupBot/MultiGroupHubBot/
  SitInBot）に作り直した（CLAUDE.md落とし穴⑨）。いずれも組員のリストを
  コンストラクタで渡す必要があるため、BOT_REGISTRYには登録せず
  sim/scenarios.pyが直接インスタンス化する。
- 借入3通りBot（bots/loan_bot.py）を LoanFixedBot(loan=...) に一本化した
  （120万/400万/1000万など、借入額を自由に指定できる）。
- bots/oversell_bot.py（OversellBot/SignerBot、V10用）を追加した。
"""

from bots.always_no_bot import AlwaysNoBot
from bots.always_yes_bot import AlwaysYesBot
from bots.follow_bot import FollowMajorityBot, FollowMinorityBot
from bots.random_vote_bot import RandomVoteBot

# Bot名 → クラスのレジストリ（simulate.py/dry_run.pyのrosterオプション用）
# 既存テスト（tests/test_game_loop.py, scripts/dry_run.py --bots）が
# DEFAULT_ROSTER=["Random","AlwaysYes"]を前提にしているため、新規Botを
# 追加してもDEFAULT_ROSTERはそのまま維持する。引数なしでseedだけ渡して
# 作れるBotだけを登録する（組・借入額・相手IDなどを要するBotは
# sim/scenarios.pyが直接インスタンス化する）。
BOT_REGISTRY: dict[str, type] = {
    "Random": RandomVoteBot,
    "AlwaysYes": AlwaysYesBot,
    "AlwaysNo": AlwaysNoBot,
    "FollowMinority": FollowMinorityBot,
    "FollowMajority": FollowMajorityBot,
}

# デフォルトロスター（既存の挙動を変えないため従来の2種のまま固定）
DEFAULT_ROSTER: list[str] = ["Random", "AlwaysYes"]
