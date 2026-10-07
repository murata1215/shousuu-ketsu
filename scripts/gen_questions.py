"""
質問生成の動作確認コマンド（§5）

出題AIへ12問をまとめて作らせ、機械検査・予備補完を済ませた結果を画面に
表示するだけのコマンド。履歴ファイル（data/question_history.jsonl）には
一切書き込まない（§5.3「Botシミュレーションでは出題AIを呼ばず、履歴ファイル
にも書かない」と同じ扱いを、動作確認コマンドにも適用する）。

使用方法:
    uv run python scripts/gen_questions.py                      # 既定モデル(DR_HAIKU)
    uv run python scripts/gen_questions.py --model L7            # モデル指定
    uv run python scripts/gen_questions.py --questions path.txt  # 固定セットの検証のみ
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from engine.config import GameConfig  # noqa: E402
from llm.questions import generate_questions, load_questions_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="llm/models.py::MODEL_REGISTRY のキー（既定: config.question_model）")
    parser.add_argument("--seed", type=int, default=0, help="予備選択の乱数シード")
    parser.add_argument("--questions", default=None, help="固定の質問セットファイル（1行1問）。指定時はAIを呼ばず検証のみ行う")
    args = parser.parse_args()

    if args.questions:
        questions = load_questions_file(args.questions)
        print(f"固定セット {args.questions} を読み込み、検査OK（{len(questions)}問）")
        for i, q in enumerate(questions, start=1):
            print(f"  {i:2d}. {q}")
        return

    config = GameConfig.default_12()
    result = generate_questions(config, model_key=args.model, seed=args.seed)

    model_key = args.model or config.question_model
    print(f"モデル: {model_key}")
    print(f"AI採用: {result.sources.count('ai')}問 / 予備補完: {result.fallback_used}問")
    if result.error:
        print(f"呼び出しエラー: {result.error}")
    print()
    for i, (q, src) in enumerate(zip(result.questions, result.sources), start=1):
        print(f"  {i:2d}. [{src}] {q}")


if __name__ == "__main__":
    main()
