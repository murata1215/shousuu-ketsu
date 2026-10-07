"""
質問生成のテスト（§5、AIを呼ばない。偽アダプタを使う）

§12.3の受入テスト#17（出題AIの呼び出し失敗→予備で補う）・#25（直近10問と
完全一致する1問だけ差し替える）を含む。
"""

import json

import pytest

from llm.questions import (
    FALLBACK_QUESTIONS,
    MAX_QUESTION_LEN,
    QUESTION_COUNT,
    append_question,
    contains_banned_word,
    generate_questions,
    is_valid_question,
    load_questions_file,
    recent_questions,
)


class FakeQuestionAdapter:
    """.complete()が固定のテキストを返すだけの偽アダプタ"""

    def __init__(self, text: str | None = None, raise_error: Exception | None = None) -> None:
        self.text = text
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def complete(self, system, messages, max_tokens=1000, temperature=0.7, request_options=None):
        self.calls.append({"system": system, "messages": messages})
        if self.raise_error is not None:
            raise self.raise_error
        return self.text, {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}


def _ai_text(questions: list[str]) -> str:
    return json.dumps({"questions": questions}, ensure_ascii=False)


VALID_12 = [
    "質問Aである", "質問Bである", "質問Cである", "質問Dである",
    "質問Eである", "質問Fである", "質問Gである", "質問Hである",
    "質問Iである", "質問Jである", "質問Kである", "質問Lである",
]


# --- 機械検査: 条件の各項目 ---

def test_is_valid_question_accepts_normal_question():
    assert is_valid_question("カラスの色は黒である") is True


def test_is_valid_question_rejects_too_long():
    assert is_valid_question("あ" * (MAX_QUESTION_LEN + 1)) is False


def test_is_valid_question_accepts_exactly_max_len():
    assert is_valid_question("あ" * MAX_QUESTION_LEN) is True


def test_is_valid_question_rejects_non_string():
    assert is_valid_question(123) is False
    assert is_valid_question(None) is False


def test_contains_banned_word_detects_banned_terms():
    assert contains_banned_word("今日の政治ニュースはYESである") is True
    assert contains_banned_word("カラスの色は黒である") is False


def test_is_valid_question_rejects_banned_word():
    assert is_valid_question("選挙には必ず行くべきである") is False


# --- 生成本体: 正常系・検査落ち・予備補完 ---

def test_generate_questions_accepts_12_valid_ai_questions(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_12))
    result = generate_questions(adapter=adapter, history_path=history, seed=1)
    assert result.questions == VALID_12
    assert result.sources == ["ai"] * 12
    assert result.fallback_used == 0
    assert len(result.questions) == QUESTION_COUNT


def test_generate_questions_rejects_too_long_question_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    too_long = VALID_12[:11] + ["あ" * (MAX_QUESTION_LEN + 1)]
    adapter = FakeQuestionAdapter(text=_ai_text(too_long))
    result = generate_questions(adapter=adapter, history_path=history, seed=2)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    assert result.sources.count("ai") == 11
    # 全問が検査を通る（40字以内）こと
    for q in result.questions:
        assert len(q) <= MAX_QUESTION_LEN


def test_generate_questions_rejects_banned_word_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    banned = VALID_12[:11] + ["宗教について考えるのは良いことである"]
    adapter = FakeQuestionAdapter(text=_ai_text(banned))
    result = generate_questions(adapter=adapter, history_path=history, seed=3)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    for q in result.questions:
        assert contains_banned_word(q) is False


def test_generate_questions_rejects_internal_duplicate_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    dup = VALID_12[:11] + [VALID_12[0]]  # 12問どうしの重複（先頭と重複）
    adapter = FakeQuestionAdapter(text=_ai_text(dup))
    result = generate_questions(adapter=adapter, history_path=history, seed=4)
    assert len(result.questions) == QUESTION_COUNT
    assert len(set(result.questions)) == QUESTION_COUNT  # 重複が残っていない
    assert result.fallback_used == 1


def test_generate_questions_rejects_too_few_questions_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_12[:8]))  # 12問に足りない
    result = generate_questions(adapter=adapter, history_path=history, seed=5)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 4
    assert result.sources.count("ai") == 8


# --- §12.3 #17: 呼び出し失敗 → 予備で補い、直近10問を避けて12問、試合は続行 ---

def test_call_failure_falls_back_to_pool_avoiding_recent(tmp_path):
    history = tmp_path / "question_history.jsonl"
    recent = list(FALLBACK_QUESTIONS[:10])
    with history.open("w", encoding="utf-8") as f:
        for i, q in enumerate(recent):
            f.write(json.dumps({"question": q, "game_id": "g0", "round_num": i + 1}) + "\n")

    adapter = FakeQuestionAdapter(raise_error=RuntimeError("API down"))
    result = generate_questions(adapter=adapter, history_path=history, seed=6)

    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == QUESTION_COUNT
    assert result.error is not None
    assert "API down" in result.error
    for q in result.questions:
        assert q not in recent


def test_call_failure_does_not_raise(tmp_path):
    """呼び出し失敗が例外として外に漏れないこと（試合を止めない）"""
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(raise_error=ValueError("boom"))
    result = generate_questions(adapter=adapter, history_path=history, seed=7)
    assert len(result.questions) == QUESTION_COUNT


# --- §12.3 #25: 直近10問と完全一致する質問は、その1問だけ予備と差し替える ---

def test_exact_match_with_recent_history_replaces_only_that_one(tmp_path):
    history = tmp_path / "question_history.jsonl"
    recent = list(FALLBACK_QUESTIONS[:10])
    with history.open("w", encoding="utf-8") as f:
        for i, q in enumerate(recent):
            f.write(json.dumps({"question": q, "game_id": "g0", "round_num": i + 1}) + "\n")

    # 12問中1問だけ直近10問の先頭と完全一致、残り11問は新規
    ai_12 = [recent[0]] + VALID_12[:11]
    adapter = FakeQuestionAdapter(text=_ai_text(ai_12))
    result = generate_questions(adapter=adapter, history_path=history, seed=8)

    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    # 一致した1問は結果に含まれない
    assert recent[0] not in result.questions
    # 残り11問はそのまま残る
    for q in VALID_12[:11]:
        assert q in result.questions


# --- プレイヤー・試合状況を渡さないこと（§5.1） ---

def test_prompt_does_not_contain_player_or_match_state(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter, history_path=history, seed=9)
    assert len(adapter.calls) == 1
    sent = adapter.calls[0]["system"] + adapter.calls[0]["messages"][0]["content"]
    for forbidden in ("P01", "player_id", "cash", "現金", "借金"):
        assert forbidden not in sent


def test_generate_questions_calls_adapter_exactly_once(tmp_path):
    """試合前に1回だけ呼ぶ（ラウンドごとには呼ばない、§5.1）"""
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter, history_path=history, seed=10)
    assert len(adapter.calls) == 1


# --- サイクル2.0: 質問生成の指示文（クイズ・豆知識にしない／見本5問） ---

def test_prompt_bans_quiz_and_trivia_wording(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter, history_path=history, seed=11)
    user_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "クイズや豆知識の問題にしない" in user_prompt


def test_prompt_includes_5_fallback_samples_reproducible_by_seed(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter_a = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter_a, history_path=history, seed=20)
    adapter_b = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter_b, history_path=history, seed=20)

    prompt_a = adapter_a.calls[0]["messages"][0]["content"]
    prompt_b = adapter_b.calls[0]["messages"][0]["content"]
    assert prompt_a == prompt_b  # 同じseedなら見本も同じ（再現可能）

    samples_in_prompt = [q for q in FALLBACK_QUESTIONS if q in prompt_a]
    assert len(samples_in_prompt) == 5

    # 違うseedなら見本の選び方が変わりうる
    adapter_c = FakeQuestionAdapter(text=_ai_text(VALID_12))
    generate_questions(adapter=adapter_c, history_path=history, seed=21)
    prompt_c = adapter_c.calls[0]["messages"][0]["content"]
    assert prompt_c != prompt_a


# --- 履歴への追記 ---

def test_append_question_then_recent_questions_reads_it_back(tmp_path):
    history = tmp_path / "question_history.jsonl"
    append_question("テスト質問1", game_id="g1", round_num=1, history_path=history)
    append_question("テスト質問2", game_id="g1", round_num=2, history_path=history)
    assert recent_questions(history) == ["テスト質問1", "テスト質問2"]


def test_recent_questions_returns_last_n_only(tmp_path):
    history = tmp_path / "question_history.jsonl"
    for i in range(15):
        append_question(f"質問{i}", game_id="g1", round_num=i + 1, history_path=history)
    recent = recent_questions(history, n=10)
    assert len(recent) == 10
    assert recent == [f"質問{i}" for i in range(5, 15)]


def test_recent_questions_missing_file_returns_empty(tmp_path):
    history = tmp_path / "does_not_exist.jsonl"
    assert recent_questions(history) == []


def test_append_question_creates_parent_dir(tmp_path):
    history = tmp_path / "nested" / "question_history.jsonl"
    append_question("質問X", game_id="g1", round_num=1, history_path=history)
    assert history.exists()


# --- 固定の質問セット（--questions） ---

def test_load_questions_file_reads_lines(tmp_path):
    f = tmp_path / "questions.txt"
    f.write_text(
        "# コメント行\n"
        "質問1である\n"
        "\n"
        "質問2である\n",
        encoding="utf-8",
    )
    assert load_questions_file(f, expected=2) == ["質問1である", "質問2である"]


def test_load_questions_file_wrong_count_raises(tmp_path):
    f = tmp_path / "questions.txt"
    f.write_text("質問1である\n質問2である\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_questions_file(f, expected=12)
