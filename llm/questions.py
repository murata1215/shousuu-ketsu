"""
質問生成モジュール（§5）

試合開始前に1回、出題AIへ12問をまとめて生成させる（§5.1）。出題AIには
直近10問（data/question_history.jsonl の末尾10行）だけを渡し、プレイヤーや
試合の状況は一切渡さない。

検査はコードでできるものだけに絞る（§5.3）: 12問あるか／40字以内か／
禁止語／完全一致の重複（12問どうし、および直近10問と）。「意味が似すぎて
いるか」はコードで判定せず、出題AI自身への指示に任せる。

検査に落ちた質問・呼び出し失敗は、内蔵の予備質問リスト（FALLBACK_QUESTIONS、
36問）から直近10問を避けて補う。試合は止めない（例外を外に出さない）。

CLAUDE.md「仕様書に書いていないことを決める必要が出たら一覧にして報告する」:
「実在の人物」は固有名詞を機械的に網羅できないため、BANNED_WORDSでは
機械判定できる語幹（政治・宗教・差別・性・暴力に関する語）だけを扱い、
実在の人物への言及は出題AIへの指示文（_build_question_prompt）と
予備リストの作成方針（FALLBACK_QUESTIONSに人名を含めない）で担保する。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_QUESTION_LEN = 40
QUESTION_COUNT = 12
HISTORY_SIZE = 10
DEFAULT_HISTORY_PATH = Path("data/question_history.jsonl")

# 禁止語（§5.2: 政治・宗教・実在の人物・差別・性・暴力に触れない）。
# 機械的に判定できる語幹のみ。
BANNED_WORDS: tuple[str, ...] = (
    "政治", "選挙", "政党", "首相", "大統領", "内閣",
    "宗教", "信仰", "教会", "聖書", "経典", "仏教", "キリスト", "イスラム",
    "死ね", "殺す", "殺し", "レイプ", "セックス", "エロ", "強姦",
    "差別", "障害者", "部落", "黒人", "白人", "女のくせに", "男のくせに",
    "戦争", "暴力", "殴る", "殴れ", "虐待", "暴行",
)

# 予備質問リスト（§5.3: 30問以上）。くだらなくて、誰でも意味が分かり、
# 政治・宗教・実在の人物・差別・性・暴力に触れないものだけを収録する。
FALLBACK_QUESTIONS: tuple[str, ...] = (
    "カラスの色は黒である",
    "魚は木に登る",
    "目玉焼きには醤油である",
    "宇宙のどこかに宇宙人がいる",
    "きのこの山よりたけのこの里である",
    "卵焼きは甘いほうがうまい",
    "猫は液体である",
    "月にはうさぎが住んでいる",
    "ラーメンのスープは最後まで飲む",
    "傘は折りたたみのほうが便利である",
    "パンダは意外とこわい",
    "カレーは一晩置いたほうがうまい",
    "冷やし中華にマヨネーズは合う",
    "靴ひもは1日1回はほどける",
    "エスカレーターでは歩かない",
    "月曜の朝はいつもより体が重い",
    "食パンの耳は残す",
    "ペンギンは泳ぐより歩くほうが速い",
    "鏡の中の自分は本物より格好いい",
    "コーヒーは紙コップのほうがうまい",
    "寝る前のスマホはやめられない",
    "自分の寝言は信用できない",
    "長風呂のほうが得である",
    "たい焼きは頭から食べる",
    "電池は振ると少し復活する",
    "犬は飼い主の顔を覚えている",
    "ゾウはネズミをこわがる",
    "洗濯物は夜に干しても乾く",
    "消しゴムは最後まで使い切れない",
    "自転車のベルはほとんど鳴らさない",
    "氷は丸いほうが溶けにくい",
    "味噌汁の具は豆腐が一番である",
    "新しい靴は最初だけ痛い",
    "鉛筆はボールペンより書きやすい",
    "枕は低いほうがよく眠れる",
    "雨の匂いは分かる",
)


def contains_banned_word(question: str) -> bool:
    """§5.2の禁止語を含むかどうか（機械判定できる語幹だけ）"""
    return any(w in question for w in BANNED_WORDS)


def is_valid_question(question: Any) -> bool:
    """§5.2の機械判定可能な条件（型・文字数・禁止語）だけを確認する。重複判定は別途行う"""
    if not isinstance(question, str):
        return False
    q = question.strip()
    if not q or len(q) > MAX_QUESTION_LEN:
        return False
    if contains_banned_word(q):
        return False
    return True


def _validate_fallback_pool() -> None:
    """起動時の自己検査: 予備リスト自身が§5.2/§5.3の条件を満たすこと（CLAUDE.md過去の落とし穴③対策の一種）"""
    if len(FALLBACK_QUESTIONS) < 30:
        raise AssertionError(
            f"FALLBACK_QUESTIONS must have at least 30 entries (got {len(FALLBACK_QUESTIONS)})"
        )
    if len(set(FALLBACK_QUESTIONS)) != len(FALLBACK_QUESTIONS):
        raise AssertionError("FALLBACK_QUESTIONS must not contain duplicates")
    for q in FALLBACK_QUESTIONS:
        if not is_valid_question(q):
            raise AssertionError(f"FALLBACK_QUESTIONS entry fails validation: {q!r}")


@dataclass
class QuestionSet:
    """12問生成の結果"""

    questions: list[str]
    sources: list[str]
    """質問ごとの出自（"ai" または "fallback"）。questionsと同じ長さ・同じ順序"""
    fallback_used: int
    """予備リストから補った件数"""
    raw_text: str | None = None
    """出題AIの生応答（試合ログ保存用。Noneなら呼び出し自体をしていない/失敗した）"""
    error: str | None = None
    """呼び出し失敗時の概要（devlog用。APIキー等の秘密情報は含めない）"""


def load_questions_file(path: str | Path, expected: int = QUESTION_COUNT) -> list[str]:
    """
    固定の質問セットをファイルから読む（§5.3: --questions、再試合・Bot検証用）

    形式: 1行1問。空行と「#」始まりの行は無視する。
    """
    lines: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    if len(lines) != expected:
        raise ValueError(f"{path}: expected {expected} questions, got {len(lines)}")
    return lines


def recent_questions(history_path: str | Path = DEFAULT_HISTORY_PATH, n: int = HISTORY_SIZE) -> list[str]:
    """
    履歴ファイルの末尾n行から質問文だけを取り出す（§5.1: 直近10問）

    ファイルが存在しない場合は空リスト（初回試合）。壊れた行は無視する。
    """
    path = Path(history_path)
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    result: list[str] = []
    for ln in lines[-n:]:
        try:
            data = json.loads(ln)
        except json.JSONDecodeError:
            continue
        q = data.get("question") if isinstance(data, dict) else None
        if isinstance(q, str):
            result.append(q)
    return result


def append_question(
    question: str,
    *,
    game_id: str,
    round_num: int,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
) -> None:
    """
    実際にラウンドで公開した質問を履歴ファイルへ1行追記する（§5.1）

    Game.on_question_published から呼ぶことを想定（AI試合のみ。Bot試合・
    動作確認では呼ばない）。ファイルは消さずに伸ばし続け、git管理からは外す
    （data/はリポジトリの.gitignoreで既に除外済み）。
    """
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "question": question,
        "game_id": game_id,
        "round_num": round_num,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _extract_questions_json(text: str) -> list[str] | None:
    """出題AIの応答からquestionsのリストを取り出す（コードフェンス剥がし＋最小限のサルベージ）"""
    candidates = [text]
    m = _FENCE_RE.search(text)
    if m:
        candidates.insert(0, m.group(1))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("questions"), list):
            return [q for q in data["questions"] if isinstance(q, str)]
        if isinstance(data, list):
            return [q for q in data if isinstance(q, str)]
    return None


def _build_question_prompt(recent: list[str]) -> tuple[str, str]:
    """出題AIへ送る(system, user)プロンプトを作る（§5.1: プレイヤー・試合状況は渡さない）"""
    system = (
        "あなたは「くだらない質問」を作るAIです。これから出す指示にだけ従い、"
        "JSON形式で質問のリストだけを返してください。"
    )
    recent_block = "\n".join(f"- {q}" for q in recent) if recent else "（まだ履歴はありません）"
    user = (
        f"YES/NOで答えられる、くだらない断定文を{QUESTION_COUNT}問作ってください。\n"
        "条件:\n"
        f"- 1問は{MAX_QUESTION_LEN}字以内\n"
        "- 予備知識なしで誰でも意味が分かる\n"
        "- 政治・宗教・実在の人物・差別・性・暴力に一切触れない\n"
        f"- {QUESTION_COUNT}問どうしで内容やテーマが重複しない\n"
        "- 以下の「直近使った質問」とは内容がかぶらないようにする\n\n"
        f"## 直近使った質問\n{recent_block}\n\n"
        "出力は必ず次のJSON形式だけにしてください: "
        f'{{"questions": ["質問1", "質問2", ... （{QUESTION_COUNT}個）]}}'
    )
    return system, user


def _select_fallback(count: int, avoid: set[str], seed: int) -> list[str]:
    """予備質問リストから直近10問・既に選んだ問を避けてcount問を選ぶ（§5.3）"""
    pool = [q for q in FALLBACK_QUESTIONS if q not in avoid]
    if len(pool) < count:
        # 予備だけでも足りない極端なケース（通常は起きない: 36問 vs avoidは最大22件）。
        # §5.3「試合は止めない」を最優先し、avoidを無視してでも埋める。
        pool = list(FALLBACK_QUESTIONS)
    rng = random.Random(seed)
    shuffled = list(pool)
    rng.shuffle(shuffled)
    return shuffled[:count]


def generate_questions(
    config: Any = None,
    *,
    adapter: Any = None,
    model_key: str | None = None,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    count: int = QUESTION_COUNT,
    seed: int = 0,
    max_tokens: int = 2000,
) -> QuestionSet:
    """
    試合前に1回、出題AIへ12問をまとめて作らせ、機械検査・予備補完まで行う（§5）

    Args:
        config: GameConfig（question_model の既定値取得に使う。adapter未指定時のみ参照）
        adapter: .complete(system, messages, max_tokens, temperature, request_options)
            を持つアダプタ（テスト用に偽アダプタを注入できる）。Noneならmodel_keyから
            llm.adapters.create_adapter() で自動生成する
        model_key: llm/models.py::MODEL_REGISTRY のキー。Noneなら config.question_model
            （configもNoneなら "DR_HAIKU"）
        history_path: 直近10問の取得元（data/question_history.jsonl）
        count: 生成数（既定12、§5.1）
        seed: 予備選択の乱数シード（再現性のため試合シードを渡すことを想定）
        max_tokens: 出題AI呼び出しのmax_tokens

    Returns:
        QuestionSet（12問・各問の出自・予備使用数・生AI応答・エラー概要）。
        例外は外に出さない（呼び出し失敗は試合を止めない、§5.3）。
    """
    _validate_fallback_pool()
    recent = recent_questions(history_path, HISTORY_SIZE)
    recent_set = set(recent)

    raw_text: str | None = None
    error: str | None = None
    ai_questions: list[str] = []

    try:
        active_adapter = adapter
        if active_adapter is None:
            from llm.adapters import create_adapter
            from llm.models import get_model

            key = model_key or getattr(config, "question_model", None) or "DR_HAIKU"
            model_info = get_model(key)
            active_adapter = create_adapter(model_info)
        system, user = _build_question_prompt(recent)
        text, _usage = active_adapter.complete(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=0.9,
        )
        raw_text = text
        if text:
            extracted = _extract_questions_json(text)
            if extracted:
                ai_questions = extracted
    except Exception as e:  # noqa: BLE001 — 呼び出し失敗は試合を止めない（§5.3）
        error = f"{type(e).__name__}: {e}"

    accepted: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()

    for q in ai_questions:
        if len(accepted) >= count:
            break
        if not is_valid_question(q):
            continue
        if q in recent_set or q in seen:
            continue
        accepted.append(q)
        sources.append("ai")
        seen.add(q)

    fallback_needed = count - len(accepted)
    if fallback_needed > 0:
        avoid = recent_set | seen
        for q in _select_fallback(fallback_needed, avoid, seed):
            accepted.append(q)
            sources.append("fallback")
            seen.add(q)

    return QuestionSet(
        questions=accepted[:count],
        sources=sources[:count],
        fallback_used=sources[:count].count("fallback"),
        raw_text=raw_text,
        error=error,
    )
