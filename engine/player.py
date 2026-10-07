"""
プレイヤー状態管理モジュール

§3に基づく資金管理（現金・2種の借金・残り借入枠）を提供する。
利息計算・借金上限の判定はすべて整数の計算で行い、浮動小数点の乗算は
一切使わない（CLAUDE.md: 「利息などの計算に小数を使わず、整数の計算で
切り上げる」）。利率は engine/config.py で (分子, 分母) の整数比として
持ち、切り上げ除算 `-(-a * num // den)` で複利を計上する。

assets_ranking() / AssetRank は dangou-card `engine/player.py`（B分類）の
`total_assets()` / `assets_ranking()` を、倍掛け預託の加算と脱落者フィルタを
外して少数決向けに単純化したもの（本作に脱落はない、§2）。それ以外
（2種債務の操作・残り借入枠・切り上げ利息）は少数決固有の新規実装（C分類）。
"""

from typing import Iterable, NamedTuple

from engine.config import GameConfig
from engine.models import PlayerState


def create_player(player_id: str, loan_amount: int) -> PlayerState:
    """
    新規プレイヤーを作成する（§3.1）

    初期配布:
    - 現金 = 借入額
    - 開始前の借金残高 = 借入額
    - 開始後の借金残高 = 0

    Args:
        player_id: プレイヤーID（例: "P01"）
        loan_amount: 借入額（120万〜1000万）
    """
    return PlayerState(
        player_id=player_id,
        cash=loan_amount,
        debt_pre=loan_amount,
        debt_post=0,
        initial_loan=loan_amount,
    )


def remaining_credit(player: PlayerState, config: GameConfig) -> int:
    """
    残り借入枠を返す（§3.4: 残り枠 = max(0, 1000万 − 借金残高の合計)）

    利息の計上で残高が上限を超えることがあるため、0未満にはクリップする。
    """
    return max(0, config.debt_cap - player.total_debt)


class PayResult(NamedTuple):
    """pay_or_borrow() の結果。支払額の内訳と取りはぐれ額を持つ"""

    player: PlayerState
    paid: int
    """実際に支払われた額（現金からの支出＋新規借入の合計）"""

    borrowed: int
    """今回新規に発生した開始後の借金（3%）の額"""

    shortfall: int
    """上限に阻まれ払われなかった額（§3.4: 受け取る側の取りはぐれ）"""


def pay_or_borrow(
    player: PlayerState, amount: int, config: GameConfig, *, cap_exempt: bool = False,
) -> PayResult:
    """
    システムへの支払い（参加費・型A/C・型Bの違約金）を、現金→開始後の借金の
    順で賄う（§3.2/§3.4）。

    - 現金で足りる分はまず現金から支払う。
    - 不足分は残り借入枠の範囲で開始後の借金（3%）にする。
    - `cap_exempt=True`（参加費専用、§3.4の例外）なら残り借入枠を無視して
      不足分を必ず貸す。
    - なお超過しても払いきれない分は shortfall として返す（取りはぐれ）。

    Args:
        player: プレイヤー状態
        amount: 支払うべき額（0以上）
        config: ゲーム設定
        cap_exempt: 借金上限の例外を適用するか（参加費のみTrue、§3.4）

    Returns:
        PayResult(更新後のplayer, 実支払額, 新規借入額, 取りはぐれ額)
    """
    if amount <= 0:
        return PayResult(player=player, paid=0, borrowed=0, shortfall=0)

    cash_used = min(player.cash, amount)
    remaining_needed = amount - cash_used

    if remaining_needed > 0:
        credit_available = (
            remaining_needed if cap_exempt else min(remaining_needed, remaining_credit(player, config))
        )
    else:
        credit_available = 0

    shortfall = remaining_needed - credit_available
    new_player = player.model_copy(update={
        "cash": player.cash - cash_used,
        "debt_post": player.debt_post + credit_available,
    })
    return PayResult(
        player=new_player, paid=cash_used + credit_available,
        borrowed=credit_available, shortfall=shortfall,
    )


def receive(player: PlayerState, amount: int) -> PlayerState:
    """現金を受け取る（Cashを増加）"""
    if amount <= 0:
        return player
    return player.model_copy(update={"cash": player.cash + amount})


def pay(player: PlayerState, amount: int) -> PlayerState:
    """
    現金を支払う（Cashを減少。借入は発生しない）

    送金（transfer、§3.2）専用。システムへの支払い（参加費等）で
    借入が必要になりうる場合は pay_or_borrow() を使うこと。
    呼び出し側（engine/actions.py::validate_action）が事前に
    `amount <= player.cash` を検証済みであること。
    """
    if amount <= 0:
        return player
    return player.model_copy(update={"cash": player.cash - amount})


def settle_negative_cash(player: PlayerState) -> PlayerState:
    """
    現金がマイナスになった分を開始後の借金にして現金を0にする（§7.1 手順7）

    1.0では契約が無いため通常は発生しないが、1.1以降の契約執行がこの
    関数を呼べるよう、独立したヘルパとして用意しておく。
    """
    if player.cash >= 0:
        return player
    shortfall = -player.cash
    return player.model_copy(update={
        "cash": 0,
        "debt_post": player.debt_post + shortfall,
    })


def repay(player: PlayerState, amount: int) -> tuple[PlayerState, int]:
    """
    任意返済を行う（§3.5、v0.3で変更）

    開始後の借金（3%）にだけ充てる。開始前の借金（1.5%）は最後まで返済
    できない。返済額は min(指定額, 現金, 開始後の借金残高) にクランプされ、
    残高を超える指定分は返済されず手元に残る。開始後の借金が0なら不成立
    （actual=0。呼び出し前に engine/actions.py::validate_action が拒否する）。

    Returns:
        (更新後のplayer, 実際に返済された額)
    """
    actual = min(amount, player.cash, player.debt_post)
    if actual <= 0:
        return player, 0
    new_player = player.model_copy(update={
        "cash": player.cash - actual,
        "debt_post": player.debt_post - actual,
    })
    return new_player, actual


def apply_interest(player: PlayerState, config: GameConfig) -> tuple[PlayerState, int, int]:
    """
    利息を計上する（§3.3）

    開始前の借金は1.5%、開始後の借金は3%、それぞれ複利・毎ラウンド切り上げ。
    そのラウンドに発生した借金（参加費の立替など）にも、同じラウンドの
    Financeから利息がつく（§3.3）。

    整数の切り上げ除算 `-(-a * num // den)` を使い、浮動小数点の乗算は
    行わない。

    Returns:
        (更新後のplayer, 開始前利息額, 開始後利息額)
    """
    interest_pre = -(-player.debt_pre * config.interest_rate_pre_num // config.interest_rate_pre_den)
    interest_post = -(-player.debt_post * config.interest_rate_post_num // config.interest_rate_post_den)
    new_player = player.model_copy(update={
        "debt_pre": player.debt_pre + interest_pre,
        "debt_post": player.debt_post + interest_post,
    })
    return new_player, interest_pre, interest_post


class AssetRank(NamedTuple):
    """自己順位通知（§7.3）用の順位情報。金額フィールドは意図的に持たない"""

    rank: int
    tied: bool
    n_players: int


def assets_ranking(players: Iterable[PlayerState]) -> dict[str, AssetRank]:
    """
    全プレイヤーの最終資産（net_assets、§2/§7.3と同一定義）から
    競技順位（standard competition ranking）を計算する。

    同額は同順位、次の順位は同額者数分スキップする（1,2,2,4）。
    本作に脱落はないため、分母（n_players）は常に全プレイヤー数。
    並びは (-net_assets, player_id) で決定的にする。

    Returns:
        player_id -> AssetRank の辞書
    """
    all_players = list(players)
    if not all_players:
        return {}
    n_players = len(all_players)
    ordered = sorted(all_players, key=lambda p: (-p.net_assets, p.player_id))
    result: dict[str, AssetRank] = {}
    rank = 1
    i = 0
    while i < len(ordered):
        current_assets = ordered[i].net_assets
        j = i
        while j < len(ordered) and ordered[j].net_assets == current_assets:
            j += 1
        group_size = j - i
        tied = group_size > 1
        for k in range(i, j):
            result[ordered[k].player_id] = AssetRank(rank=rank, tied=tied, n_players=n_players)
        rank += group_size
        i = j
    return result
