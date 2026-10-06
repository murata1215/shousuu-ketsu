"""
自動代行Commitモジュール（§4.4）

dangou-card `engine/autocommit.py`（B分類）の構造（型Bの指定があれば従う→
無ければ乱数）を踏襲しつつ、本サイクルには契約（§6、型B）が存在しないため
`type_b_constraint` は常にNoneを渡す呼び出しになる。1.1で契約を追加した際は
呼び出し側がそのラウンドの型B義務からYES/NOを渡すだけで、本関数の判定
ロジックは変更不要（空実装によるフォワードコンパット）。
"""

from engine.models import Vote
from engine.rng import GameRng


def decide_auto_vote(rng: GameRng, type_b_constraint: Vote | None = None) -> Vote:
    """
    自動代行の投票先を決定する（§4.4）

    - 型Bで投票先の指定があれば、それに従う。
    - 指定がない場合（1.0は契約が無いため常にこちら）、試合のシード値から
      決まる乱数でYES/NOを選ぶ（再現可能）。

    「指定が矛盾している場合」（同ラウンドにYES指定とNO指定の両方を負って
    いる、§6.3）の判定は1.1で契約を追加した際に呼び出し側へ実装する
    （この関数は単一の制約値のみを受け取る設計のため、矛盾の検出自体は
    呼び出し側の責務）。

    Args:
        rng: ゲーム乱数ジェネレータ
        type_b_constraint: そのラウンドの型B義務から決まる投票先。無ければNone

    Returns:
        自動代行の投票先
    """
    if type_b_constraint is not None:
        return type_b_constraint
    return rng.random_vote()
