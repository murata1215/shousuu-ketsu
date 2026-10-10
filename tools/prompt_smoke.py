"""
tools/prompt_smoke.py — プロンプトの実AI疎通確認（サイクル1.4、最小限）

R1の交渉プロンプトを1席ぶん組み立て、指定した1モデルへ1回だけ送り、
返ってきた応答が llm/response_parser.py::parse_response() で有効な
アクションとして読めるかを確認する。費用は llm/costing.py::usage_cost() で
実測して表示する（事前見積り worst_case_cost() が --max-cost-usd を超える
場合は送信前に止める。既定0.02ドル）。

CLAUDE.md「長い処理は裏で走らせず、1本ずつ表で回す」: 1回の実行で1モデルに
1回だけ送る。複数モデルを確かめる場合は、本スクリプトを1回ずつ手動で
フォアグラウンド実行すること（並列化・一括実行はしない）。

使い方:
    uv run python tools/prompt_smoke.py --model L7
    uv run python tools/prompt_smoke.py --model DR_HAIKU
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from engine.config import GameConfig  # noqa: E402
from engine.game import Game  # noqa: E402
from engine.negotiation import StubAgent  # noqa: E402
from llm.adapters import AdapterError, create_adapter  # noqa: E402
from llm.costing import usage_cost, worst_case_cost  # noqa: E402
from llm.models import get_model  # noqa: E402
from llm.prompt_builder import build_negotiation_prompt, build_system_prompt  # noqa: E402
from llm.response_parser import ParseError, parse_response  # noqa: E402

DEFAULT_MAX_COST_USD = 0.02
DEFAULT_MAX_TOKENS = 1500


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="llm/models.py::MODEL_REGISTRY のキー（例: L7, DR_HAIKU）")
    parser.add_argument("--player-id", default="P01")
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    args = parser.parse_args()

    model_info = get_model(args.model)
    config = GameConfig.default_12()

    # R1V1開始直後の状態を再現する（StubAgent全員・AIを呼ばない範囲でGameを進める）。
    agents = {f"P{i:02d}": StubAgent() for i in range(1, config.num_players + 1)}
    game = Game(config, agents, seed=1)
    game._setup()
    game.current_round = 1
    game._phase_open(1, 1)
    visible_state = game._build_visible_state(1, 1, for_player_id=args.player_id)
    player_state = game.players[args.player_id]

    include_anonymization = model_info.adapter_type != "devrelay_http"
    system = build_system_prompt(args.player_id, config, include_anonymization=include_anonymization)
    user = build_negotiation_prompt(player_state, 1, 1, 1, visible_state, config)

    estimated = worst_case_cost(model_info, system, user, DEFAULT_MAX_TOKENS)
    print(f"モデル: {args.model} ({model_info.model_id})")
    print(f"事前見積りコスト: ${estimated:.4f}（上限 ${args.max_cost_usd:.4f}）")
    if estimated > args.max_cost_usd:
        print("見積りが上限を超えるため、送信せずに停止します。")
        return

    adapter = create_adapter(model_info)
    try:
        text, usage = adapter.complete(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=0.7,
        )
    except AdapterError as e:
        print(f"呼び出し失敗: {e}")
        return

    cost = usage_cost(model_info, usage)
    print(f"実測コスト: ${cost:.5f}")
    print("--- 応答本文 ---")
    print(text)
    print("--- 解析結果 ---")
    try:
        strategy, action = parse_response(text, args.player_id, "negotiation")
        print(f"action.type = {action.type}")
        print(f"strategy = {strategy}")
    except ParseError as e:
        print(f"解析失敗（無効な応答として扱う）: {e}")


if __name__ == "__main__":
    main()
