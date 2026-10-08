"""
経済パラメータ設定モジュール

ゲーム全体の経済パラメータを管理する。仕様書v0.4 §10の確定パラメータ一覧に対応。
サイクル4.0でv0.3（投票=ラウンド、12ラウンド、賭け金10万固定）からv0.4
（勝ち抜き制、L12R4V6: 12人・4ラウンド・1ラウンド最大6投票）へ全面差し替えた。

「暫定」と書かれた値（§11.2の8点）も、今回は表どおりに実装し、1か所
（本クラス）にまとめてあとで変えられるようにする。

利息計算に小数を使わないため、利率は (分子, 分母) の整数比で持つ
（§3.3: 開始前5%・開始後15%、毎ラウンド複利・切り上げ）。
"""

from pydantic import BaseModel


class GameConfig(BaseModel):
    """
    ゲーム経済パラメータの設定クラス（v0.4 §10）

    全パラメータはコンストラクタ引数で上書き可能。
    default_12() / dev_small() クラスメソッドで標準設定を取得できる。
    """

    # --- 基本構成（§10: L12R4V6） ---
    num_players: int = 12
    """プレイヤー数（§10: 12）"""

    num_rounds: int = 4
    """ラウンド数（§10: 4）"""

    max_votes_per_round: int = 6
    """1ラウンドの最大投票数（§4.4/§10: 6。やり直し2回→決着→やり直し2回→決着、
    または最後がやり直し3回で打ち切り）"""

    survivors_max: int = 2
    """ラウンド終了となる残り人数の上限（§4.5/§10: 決着して残りがこの人数以下
    になったらラウンド終了）"""

    max_consecutive_ties: int = 3
    """やり直し（同数・全員一致）の連続回数がこれに達したら打ち切り（§4.4/§10）。
    連続回数は決着があれば0に戻る"""

    # --- 借入・利息（§3） ---
    loan_min: int = 1_200_000
    """借入最低額（§3.1/§10: 120万円）"""

    loan_max: int = 10_000_000
    """借入最大額（§3.1/§10: 1000万円）"""

    interest_rate_pre_num: int = 5
    interest_rate_pre_den: int = 100
    """開始前の借金の利率（§3.3/§10: 5% = 5/100、毎ラウンド複利・切り上げ）。
    暫定（§11.2 #2）"""

    interest_rate_post_num: int = 15
    interest_rate_post_den: int = 100
    """開始後の借金の利率（§3.3/§10: 15% = 15/100、毎ラウンド複利・切り上げ）。
    暫定（§11.2 #2）"""

    debt_cap: int = 10_000_000
    """借金合計（2種の合算、利息込み）の上限（§3.4/§10: 1000万円）"""

    # --- 参加費・延長料（§4） ---
    entry_fee: int = 1_000_000
    """参加費（§4.1/§10: 100万円、ラウンドごとに全員）"""

    extension_fee: int = 100_000
    """延長料（§4.4/§10: 10万円、やり直しのたびに残っている人だけ）"""

    # --- 型B違約金（§6.3） ---
    penalty_amount: int = 1_000_000
    """型Bの違約金（§6.3/§10: 100万円、義務1本につき）。暫定（§11.2 #6）"""

    # --- 交渉の巡（§7.2） ---
    negotiation_max_turns_first: int = 10
    """ラウンド最初の投票（V1）の交渉の最大巡数（§7.2/§10: 10）。暫定（§11.2 #3）"""

    negotiation_max_turns_next: int = 6
    """決着の後の投票の交渉の最大巡数（§7.2/§10: 6）。暫定（§11.2 #3）"""

    negotiation_max_turns_retry: int = 3
    """やり直しの再投票の交渉の最大巡数（§7.2/§10: 3）。暫定（§11.2 #3）"""

    # --- 順位公開（§7.6） ---
    rank_public_rounds: tuple[int, ...] = (2,)
    """全員の順位を公開するラウンド（§7.6/§10: R2のFinance後）。暫定（§11.2 #8）"""

    # --- 質問生成（§5） ---
    question_model: str = "DR_HAIKU"
    """出題AIに使うモデルキー（llm/models.py::MODEL_REGISTRYのキー）。§5.1: 既定は最安級1つ"""

    recent_questions_window: int = 30
    """質問の重複を避ける直近N問の範囲（§5.1/§10: 30）。暫定（§11.2 #7）"""

    @property
    def questions_per_game(self) -> int:
        """1試合で使う質問の総数（§5.1: 4ラウンド×最大6投票=24）"""
        return self.num_rounds * self.max_votes_per_round

    @classmethod
    def default_12(cls) -> "GameConfig":
        """12人版デフォルト設定を返す（仕様書v0.4 §10の確定パラメータに準拠）"""
        return cls()

    @classmethod
    def dev_small(cls, num_players: int = 4, num_rounds: int = 2) -> "GameConfig":
        """
        開発・スモークテスト用の小規模設定を返す

        人数とラウンド数を縮小するだけで、他の経済パラメータは default_12() と
        同一。
        """
        return cls(num_players=num_players, num_rounds=num_rounds)
