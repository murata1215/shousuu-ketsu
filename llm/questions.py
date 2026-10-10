"""
質問生成モジュール（§5）

試合開始前に1回、出題AIへ24問をまとめて生成させる（§5.1、v0.4: 4ラウンド×
最大6投票=24問）。出題AIには直近30問（data/question_history.jsonl の末尾
30行）だけを渡し、プレイヤーや試合の状況は一切渡さない。

検査はコードでできるものだけに絞る（§5.3）: 24問あるか／40字以内か／
禁止語／完全一致の重複（24問どうし、および直近30問と）。「意味が似すぎて
いるか」はコードで判定せず、出題AI自身への指示に任せる。YES寄り/NO寄りの
偏りも同様にコードでは判定せず、指示文（_build_question_prompt）に
「およそ半分ずつ」の1行を足すことで出題AI自身に委ねる。

検査に落ちた質問・呼び出し失敗は、内蔵の予備質問リスト（FALLBACK_QUESTIONS_
YES_LEAN/FALLBACK_QUESTIONS_NO_LEAN、各30問以上）から直近30問を避けて
両方からおよそ半分ずつ補う。試合は止めない（例外を外に出さない）。

サイクル4.2bでv0.3（12問・直近10問固定）からv0.4（24問・直近30問）へ
変更した。既存の `data/question_history.jsonl`（v0.3形式、vote_numの無い
行）は読める（recent_questions()はquestionキーだけを見るため）。既存の行は
消さない・書き換えない（append_question()は常に追記のみ）。

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
QUESTION_COUNT = 24
"""1試合の質問数の既定値（§5.1/§10: 4ラウンド×最大6投票=24）。
generate_questions()にconfigを渡した場合はconfig.questions_per_gameを優先する"""
HISTORY_SIZE = 30
"""出題AIに見せる直近N問の既定値（§5.1/§10: 30）。
generate_questions()にconfigを渡した場合はconfig.recent_questions_windowを優先する"""
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

# 予備質問リスト（§5.3: 各30問以上）。くだらなくて、誰でも意味が分かり、
# 政治・宗教・実在の人物・差別・性・暴力に触れないものだけを収録する。
#
# サイクル4.2bで、「ふつうに考えるとYESと答えたくなる文」と「ふつうに考えると
# NOと答えたくなる文」の2本に分けた（出題AIへの指示文・予備補完の両方で
# およそ半分ずつ使うため）。YES寄り/NO寄りの分類はコードで判定せず、
# リストの作成方針（この文自体の読み心地）で担保する（§5.2と同じ扱い）。
FALLBACK_QUESTIONS_YES_LEAN: tuple[str, ...] = (
    "カラスの色は黒である",
    "新しい靴は最初だけ痛い",
    "靴ひもは1日1回はほどける",
    "消しゴムは最後まで使い切れない",
    "自分の寝言は信用できない",
    "雨の匂いは分かる",
    "月曜の朝はいつもより体が重い",
    "電池は振ると少し復活する",
    "犬は飼い主の顔を覚えている",
    "洗濯物は夜に干しても乾く",
    "長風呂のほうが得である",
    "食パンの耳は残す",
    "枕は低いほうがよく眠れる",
    "冷やし中華にマヨネーズは合う",
    "傘は折りたたみのほうが便利である",
    "寝る前のスマホはやめられない",
    "たい焼きは頭から食べる",
    "鉛筆はボールペンより書きやすい",
    "氷は丸いほうが溶けにくい",
    "味噌汁の具は豆腐が一番である",
    "新しい年度は気持ちを切り替えやすい",
    "雨の日は眠くなりやすい",
    "財布は小さいほうが使いやすい",
    "階段は上りより下りのほうが怖い",
    "歯ブラシは3か月で交換したほうがいい",
    "冬は布団から出にくい",
    "自転車は乗り始めが一番ふらつく",
    "旅行の前日はよく眠れない",
    "長時間座ると腰が痛くなる",
    "初めての道は実際より長く感じる",
)

FALLBACK_QUESTIONS_NO_LEAN: tuple[str, ...] = (
    "魚は木に登る",
    "猫は液体である",
    "月にはうさぎが住んでいる",
    "パンダは意外とこわい",
    "ペンギンは泳ぐより歩くほうが速い",
    "ゾウはネズミをこわがる",
    "自転車のベルはほとんど鳴らさない",
    "エスカレーターでは歩かない",
    "ラーメンのスープは最後まで飲む",
    "きのこの山よりたけのこの里である",
    "目玉焼きには醤油である",
    "卵焼きは甘いほうがうまい",
    "宇宙のどこかに宇宙人がいる",
    "コーヒーは紙コップのほうがうまい",
    "鏡の中の自分は本物より格好いい",
    "カレーは一晩置いたほうがうまい",
    "象は実は鳥より軽い",
    "満月の夜は人の気分が変わる",
    "右利きのほうが左利きより多才である",
    "サボテンは水をあげなくても永遠に生きる",
    "ダチョウは頭を地面に埋めて隠れる",
    "サメは泳ぎを止めると死んでしまう",
    "金魚の記憶は3秒しか持たない",
    "コウモリは目が見えない",
    "ラクダのこぶには水が入っている",
    "1円玉は水に浮く",
    "虹は手で触れられるところまで近づける",
    "雷は同じ場所に二度落ちない",
    "ダイヤモンドは燃えない",
    "ガムは飲み込むと胃の中に何年も残る",
)

FALLBACK_QUESTIONS: tuple[str, ...] = FALLBACK_QUESTIONS_YES_LEAN + FALLBACK_QUESTIONS_NO_LEAN
"""YES寄り・NO寄りを合わせた全予備問（既存コード・テストからの参照用）"""


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
    """
    起動時の自己検査: 予備リスト自身が§5.2/§5.3の条件を満たすこと
    （CLAUDE.md過去の落とし穴③対策の一種）

    YES寄り・NO寄りそれぞれ30問以上、合わせて重複なし、全問が機械判定を通る
    こと（§5.1のおよそ半分ずつの補完が常に可能であることを保証する）。
    """
    for name, pool in (
        ("FALLBACK_QUESTIONS_YES_LEAN", FALLBACK_QUESTIONS_YES_LEAN),
        ("FALLBACK_QUESTIONS_NO_LEAN", FALLBACK_QUESTIONS_NO_LEAN),
    ):
        if len(pool) < 30:
            raise AssertionError(f"{name} must have at least 30 entries (got {len(pool)})")
    if len(set(FALLBACK_QUESTIONS)) != len(FALLBACK_QUESTIONS):
        raise AssertionError("FALLBACK_QUESTIONS (YES_LEAN+NO_LEAN) must not contain duplicates")
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
    vote_num: int,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
) -> None:
    """
    実際に投票で公開した質問を履歴ファイルへ1行追記する（§5.1）

    Game.on_question_published から呼ぶことを想定（AI試合のみ。Bot試合・
    動作確認では呼ばない）。ファイルは消さずに伸ばし続け、git管理からは外す
    （data/はリポジトリの.gitignoreで既に除外済み）。

    v0.4でvote_numを足した（§1.1: 1ラウンドに複数の投票があるため、
    ラウンド番号だけでは質問の使用タイミングを特定できない）。既存の
    v0.3形式の行（vote_numが無い）は書き換えない・消さない（追記のみ）。
    """
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "question": question,
        "game_id": game_id,
        "round_num": round_num,
        "vote_num": vote_num,
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


def _build_question_prompt(
    recent: list[str], samples: list[str] | None = None, count: int = QUESTION_COUNT,
) -> tuple[str, str]:
    """出題AIへ送る(system, user)プロンプトを作る（§5.1: プレイヤー・試合状況は渡さない）"""
    system = (
        "あなたは「くだらない質問」を作るAIです。これから出す指示にだけ従い、"
        "JSON形式で質問のリストだけを返してください。"
    )
    recent_block = "\n".join(f"- {q}" for q in recent) if recent else "（まだ履歴はありません）"
    samples_block = (
        "\n".join(f"- {q}" for q in samples) if samples else "（見本はありません）"
    )
    half = count // 2
    user = (
        f"YES/NOで答えられる、くだらない断定文を{count}問作ってください。\n"
        "条件:\n"
        f"- 1問は{MAX_QUESTION_LEN}字以内\n"
        "- 予備知識なしで誰でも意味が分かる\n"
        "- クイズや豆知識の問題にしない。くだらない、どうでもいい断定にする\n"
        "- 政治・宗教・実在の人物・差別・性・暴力に一切触れない\n"
        f"- {count}問どうしで内容やテーマが重複しない\n"
        "- 以下の「直近使った質問」とは内容がかぶらないようにする\n"
        "- ふつうに考えるとYESと答えたくなる文と、ふつうに考えるとNOと答えたくなる文を、"
        f"およそ半分ずつ（{half}問ずつ前後）にする。並び順は混ぜてよい\n\n"
        f"## 見本（このくらいくだらない断定にする。質問の種類や分類は自由）\n{samples_block}\n\n"
        f"## 直近使った質問\n{recent_block}\n\n"
        "出力は必ず次のJSON形式だけにしてください: "
        f'{{"questions": ["質問1", "質問2", ... （{count}個）]}}'
    )
    return system, user


def _select_fallback(count: int, avoid: set[str], seed: int) -> list[str]:
    """
    予備質問リストから直近N問・既に選んだ問を避けてcount問を選ぶ（§5.3）

    YES寄り・NO寄りの両方からおよそ半分ずつ選ぶ（サイクル4.2b）。片方が
    avoidで足りなくなった場合は、もう片方で埋める（§5.3「試合は止めない」
    を最優先し、それでも足りなければavoidを無視してでも埋める）。
    """
    rng = random.Random(seed)
    yes_pool = [q for q in FALLBACK_QUESTIONS_YES_LEAN if q not in avoid]
    no_pool = [q for q in FALLBACK_QUESTIONS_NO_LEAN if q not in avoid]
    if len(yes_pool) + len(no_pool) < count:
        yes_pool = list(FALLBACK_QUESTIONS_YES_LEAN)
        no_pool = list(FALLBACK_QUESTIONS_NO_LEAN)
    rng.shuffle(yes_pool)
    rng.shuffle(no_pool)

    half = count // 2
    selected = yes_pool[:half] + no_pool[: count - half]
    if len(selected) < count:
        # 片方が足りなかった分を、もう片方の残りから埋める
        leftover = [q for q in (yes_pool + no_pool) if q not in selected]
        rng.shuffle(leftover)
        selected += leftover[: count - len(selected)]
    rng.shuffle(selected)
    return selected[:count]


def generate_questions(
    config: Any = None,
    *,
    adapter: Any = None,
    model_key: str | None = None,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    count: int | None = None,
    history_window: int | None = None,
    seed: int = 0,
    max_tokens: int = 2000,
) -> QuestionSet:
    """
    試合前に1回、出題AIへ24問をまとめて作らせ、機械検査・予備補完まで行う（§5）

    Args:
        config: GameConfig（question_model・questions_per_game・
            recent_questions_window の既定値取得に使う。adapter未指定時のみ参照）
        adapter: .complete(system, messages, max_tokens, temperature, request_options)
            を持つアダプタ（テスト用に偽アダプタを注入できる）。Noneならmodel_keyから
            llm.adapters.create_adapter() で自動生成する
        model_key: llm/models.py::MODEL_REGISTRY のキー。Noneなら config.question_model
            （configもNoneなら "DR_HAIKU"）
        history_path: 直近N問の取得元（data/question_history.jsonl）
        count: 生成数。Noneならconfig.questions_per_game（configもNoneなら
            QUESTION_COUNT=24、§5.1）
        history_window: 直近N問の取得件数。Noneならconfig.recent_questions_window
            （configもNoneならHISTORY_SIZE=30）
        seed: 予備選択の乱数シード（再現性のため試合シードを渡すことを想定）
        max_tokens: 出題AI呼び出しのmax_tokens

    Returns:
        QuestionSet（count問・各問の出自・予備使用数・生AI応答・エラー概要）。
        例外は外に出さない（呼び出し失敗は試合を止めない、§5.3）。
    """
    _validate_fallback_pool()
    effective_count = count if count is not None else (
        getattr(config, "questions_per_game", None) or QUESTION_COUNT
    )
    effective_window = history_window if history_window is not None else (
        getattr(config, "recent_questions_window", None) or HISTORY_SIZE
    )
    recent = recent_questions(history_path, effective_window)
    recent_set = set(recent)
    sample_rng = random.Random(seed)
    half = 5 // 2
    yes_samples = sample_rng.sample(
        FALLBACK_QUESTIONS_YES_LEAN, k=min(half, len(FALLBACK_QUESTIONS_YES_LEAN)),
    )
    no_samples = sample_rng.sample(
        FALLBACK_QUESTIONS_NO_LEAN, k=min(5 - half, len(FALLBACK_QUESTIONS_NO_LEAN)),
    )
    samples = yes_samples + no_samples

    raw_text: str | None = None
    error: str | None = None
    ai_questions: list[str] = []
    count = effective_count

    try:
        active_adapter = adapter
        if active_adapter is None:
            from llm.adapters import create_adapter
            from llm.models import get_model

            key = model_key or getattr(config, "question_model", None) or "DR_HAIKU"
            model_info = get_model(key)
            active_adapter = create_adapter(model_info)
        system, user = _build_question_prompt(recent, samples, count=effective_count)
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
