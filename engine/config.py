"""
経済パラメータ設定モジュール

ゲーム全体の経済パラメータを管理する。仕様書§10の確定パラメータ一覧に対応。
dangou-card `engine/config.py`（B分類）から、12人12R・少数決に残る項目だけを
抜き出した。市場・賞金・生還現金・報奨・匿名通信・倍掛け・強制返済・
カードトレード・霧・二週目（veteran）・free_cash_mode は全て削除する
（少数決の仕様に存在しないため。CLAUDE.md 過去の落とし穴③の回避）。

利息計算に小数を使わないため、利率は (分子, 分母) の整数比で持つ
（§3.3: 1.5%複利 / 3%複利、毎ラウンド切り上げ）。
"""

from pydantic import BaseModel


class GameConfig(BaseModel):
    """
    ゲーム経済パラメータの設定クラス

    全パラメータはコンストラクタ引数で上書き可能。
    default_12() / dev_small() クラスメソッドで標準設定を取得できる。
    """

    # --- 基本構成（§10） ---
    num_players: int = 12
    """プレイヤー数（§10: 12）"""

    num_rounds: int = 12
    """ラウンド数（§10: 12）"""

    # --- 借入・利息（§3） ---
    loan_min: int = 1_200_000
    """借入最低額（§3.1/§10: 120万円）"""

    loan_max: int = 10_000_000
    """借入最大額（§3.1/§10: 1000万円）"""

    interest_rate_pre_num: int = 15
    interest_rate_pre_den: int = 1000
    """開始前の借金の利率（§3.3: 1.5% = 15/1000、毎ラウンド複利・切り上げ）"""

    interest_rate_post_num: int = 3
    interest_rate_post_den: int = 100
    """開始後の借金の利率（§3.3: 3% = 3/100、毎ラウンド複利・切り上げ）"""

    debt_cap: int = 10_000_000
    """借金合計（2種の合算、利息込み）の上限（§3.4/§10: 1000万円）"""

    # --- 参加費・投票（§4） ---
    entry_fee: int = 100_000
    """参加費（§4.1/§10: 10万円、固定。Commit時に徴収）"""

    penalty_amount: int = 1_000_000
    """型Bの違約金（§6.3/§10: 100万円、義務1本につき）。サイクル1.1で使用開始"""

    # --- 交渉（§7） ---
    negotiation_max_turns: int = 10
    """交渉の最大巡数（§7/§10: 10）"""

    # --- 順位公開（§7.3） ---
    rank_public_rounds: tuple[int, ...] = (3, 6, 9)
    """全員の順位を公開するラウンド（§7.3: R3・R6・R9終了後のFinance後）"""

    # --- 質問生成（§5） ---
    question_model: str = "DR_HAIKU"
    """出題AIに使うモデルキー（llm/models.py::MODEL_REGISTRYのキー）。§5.1: 既定は最安級1つ"""

    @classmethod
    def default_12(cls) -> "GameConfig":
        """12人版デフォルト設定を返す（仕様書§10の確定パラメータに準拠）"""
        return cls()

    @classmethod
    def dev_small(cls, num_players: int = 4, num_rounds: int = 4) -> "GameConfig":
        """
        開発・スモークテスト用の小規模設定を返す

        人数とラウンド数を縮小するだけで、他の経済パラメータは default_12() と
        同一。
        """
        return cls(num_players=num_players, num_rounds=num_rounds)
