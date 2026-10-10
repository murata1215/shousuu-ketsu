"""
質問生成のテスト（§5、AIを呼ばない。偽アダプタを使う）

§12.3の受入テスト#47（出題AIの呼び出し失敗→予備リストから直近30問を避けて
24問）・#48（試合で使った質問だけを履歴ファイルに追記する）を含む。
v0.4（24問・直近30問・YES寄り/NO寄りの予備リスト分離）に合わせてサイクル
4.2bで全面的に直した。
"""

import json

import pytest

from llm.questions import (
    FALLBACK_QUESTIONS,
    FALLBACK_QUESTIONS_NO_LEAN,
    FALLBACK_QUESTIONS_YES_LEAN,
    HISTORY_SIZE,
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


VALID_24 = [f"質問{chr(65 + i)}である" for i in range(24)]  # 質問Aである…質問Xである


# --- 定数（v0.4: 24問・直近30問） ---

def test_question_count_and_history_size_are_v0_4_values():
    assert QUESTION_COUNT == 24
    assert HISTORY_SIZE == 30


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


# --- 予備リストの分離（YES寄り/NO寄り、サイクル4.2b） ---

def test_fallback_pools_each_have_at_least_30_entries_and_no_overlap():
    assert len(FALLBACK_QUESTIONS_YES_LEAN) >= 30
    assert len(FALLBACK_QUESTIONS_NO_LEAN) >= 30
    assert set(FALLBACK_QUESTIONS_YES_LEAN).isdisjoint(FALLBACK_QUESTIONS_NO_LEAN)
    assert FALLBACK_QUESTIONS == FALLBACK_QUESTIONS_YES_LEAN + FALLBACK_QUESTIONS_NO_LEAN


def test_fallback_pool_entries_all_pass_validation():
    for q in FALLBACK_QUESTIONS:
        assert is_valid_question(q), f"予備問が検査を通らない: {q!r}"


# --- 生成本体: 正常系・検査落ち・予備補完（24問） ---

def test_generate_questions_accepts_24_valid_ai_questions(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    result = generate_questions(adapter=adapter, history_path=history, seed=1)
    assert result.questions == VALID_24
    assert result.sources == ["ai"] * 24
    assert result.fallback_used == 0
    assert len(result.questions) == QUESTION_COUNT


def test_generate_questions_rejects_too_long_question_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    too_long = VALID_24[:23] + ["あ" * (MAX_QUESTION_LEN + 1)]
    adapter = FakeQuestionAdapter(text=_ai_text(too_long))
    result = generate_questions(adapter=adapter, history_path=history, seed=2)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    assert result.sources.count("ai") == 23
    for q in result.questions:
        assert len(q) <= MAX_QUESTION_LEN


def test_generate_questions_rejects_banned_word_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    banned = VALID_24[:23] + ["宗教について考えるのは良いことである"]
    adapter = FakeQuestionAdapter(text=_ai_text(banned))
    result = generate_questions(adapter=adapter, history_path=history, seed=3)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    for q in result.questions:
        assert contains_banned_word(q) is False


def test_generate_questions_rejects_internal_duplicate_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    dup = VALID_24[:23] + [VALID_24[0]]  # 24問どうしの重複（先頭と重複）
    adapter = FakeQuestionAdapter(text=_ai_text(dup))
    result = generate_questions(adapter=adapter, history_path=history, seed=4)
    assert len(result.questions) == QUESTION_COUNT
    assert len(set(result.questions)) == QUESTION_COUNT  # 重複が残っていない
    assert result.fallback_used == 1


def test_generate_questions_rejects_too_few_questions_and_fills_with_fallback(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24[:16]))  # 24問に足りない
    result = generate_questions(adapter=adapter, history_path=history, seed=5)
    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 8
    assert result.sources.count("ai") == 16


def test_fallback_fill_draws_from_both_yes_and_no_lean_pools(tmp_path):
    """予備補完は、空っぽのAI応答に対してYES寄り・NO寄りの両方からおよそ半分ずつ選ぶ"""
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text([]))
    result = generate_questions(adapter=adapter, history_path=history, seed=42)
    assert result.fallback_used == QUESTION_COUNT
    n_yes = sum(1 for q in result.questions if q in FALLBACK_QUESTIONS_YES_LEAN)
    n_no = sum(1 for q in result.questions if q in FALLBACK_QUESTIONS_NO_LEAN)
    assert n_yes + n_no == QUESTION_COUNT
    assert n_yes >= 10  # 「およそ半分ずつ」（24問中12問±数問）
    assert n_no >= 10


# --- §12.3 #47: 呼び出し失敗 → 予備で補い、直近30問を避けて24問、試合は続行 ---

def test_acceptance_47_adapter_failure_falls_back_to_pool_avoiding_recent_30(tmp_path) -> None:
    history = tmp_path / "question_history.jsonl"
    recent = list(FALLBACK_QUESTIONS[:30])
    with history.open("w", encoding="utf-8") as f:
        for i, q in enumerate(recent):
            f.write(json.dumps({"question": q, "game_id": "g0", "round_num": 1, "vote_num": i + 1}) + "\n")

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


# --- 直近30問と完全一致する質問は、その1問だけ予備と差し替える ---

def test_exact_match_with_recent_history_replaces_only_that_one(tmp_path):
    history = tmp_path / "question_history.jsonl"
    recent = list(FALLBACK_QUESTIONS[:30])
    with history.open("w", encoding="utf-8") as f:
        for i, q in enumerate(recent):
            f.write(json.dumps({"question": q, "game_id": "g0", "round_num": 1, "vote_num": i + 1}) + "\n")

    # 24問中1問だけ直近30問の先頭と完全一致、残り23問は新規
    ai_24 = [recent[0]] + VALID_24[:23]
    adapter = FakeQuestionAdapter(text=_ai_text(ai_24))
    result = generate_questions(adapter=adapter, history_path=history, seed=8)

    assert len(result.questions) == QUESTION_COUNT
    assert result.fallback_used == 1
    # 一致した1問は結果に含まれない
    assert recent[0] not in result.questions
    # 残り23問はそのまま残る
    for q in VALID_24[:23]:
        assert q in result.questions


# --- プレイヤー・試合状況を渡さないこと（§5.1） ---

def test_prompt_does_not_contain_player_or_match_state(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter, history_path=history, seed=9)
    assert len(adapter.calls) == 1
    sent = adapter.calls[0]["system"] + adapter.calls[0]["messages"][0]["content"]
    for forbidden in ("P01", "player_id", "cash", "現金", "借金"):
        assert forbidden not in sent


def test_generate_questions_calls_adapter_exactly_once(tmp_path):
    """試合前に1回だけ呼ぶ（投票ごとには呼ばない、§5.1）"""
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter, history_path=history, seed=10)
    assert len(adapter.calls) == 1


# --- 指示文（クイズ・豆知識にしない／見本5問／YES寄り・NO寄りの指示） ---

def test_prompt_bans_quiz_and_trivia_wording(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter, history_path=history, seed=11)
    user_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "クイズや豆知識の問題にしない" in user_prompt


def test_prompt_requests_roughly_half_yes_half_no_leaning(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter, history_path=history, seed=11)
    user_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "YESと答えたくなる文" in user_prompt
    assert "NOと答えたくなる文" in user_prompt
    assert "およそ半分ずつ" in user_prompt


def test_prompt_includes_5_fallback_samples_from_both_pools_reproducible_by_seed(tmp_path):
    history = tmp_path / "question_history.jsonl"
    adapter_a = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter_a, history_path=history, seed=20)
    adapter_b = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter_b, history_path=history, seed=20)

    prompt_a = adapter_a.calls[0]["messages"][0]["content"]
    prompt_b = adapter_b.calls[0]["messages"][0]["content"]
    assert prompt_a == prompt_b  # 同じseedなら見本も同じ（再現可能）

    samples_in_prompt = [q for q in FALLBACK_QUESTIONS if q in prompt_a]
    assert len(samples_in_prompt) == 5
    # 両方のリストから選ばれている（どちらか一方だけに偏らない）
    assert any(q in FALLBACK_QUESTIONS_YES_LEAN for q in samples_in_prompt)
    assert any(q in FALLBACK_QUESTIONS_NO_LEAN for q in samples_in_prompt)

    # 違うseedなら見本の選び方が変わりうる
    adapter_c = FakeQuestionAdapter(text=_ai_text(VALID_24))
    generate_questions(adapter=adapter_c, history_path=history, seed=21)
    prompt_c = adapter_c.calls[0]["messages"][0]["content"]
    assert prompt_c != prompt_a


# --- 履歴への追記（vote_num入り、§1.1） ---

def test_append_question_then_recent_questions_reads_it_back(tmp_path):
    history = tmp_path / "question_history.jsonl"
    append_question("テスト質問1", game_id="g1", round_num=1, vote_num=1, history_path=history)
    append_question("テスト質問2", game_id="g1", round_num=1, vote_num=2, history_path=history)
    assert recent_questions(history) == ["テスト質問1", "テスト質問2"]


def test_append_question_writes_round_and_vote_num(tmp_path):
    history = tmp_path / "question_history.jsonl"
    append_question("テスト質問", game_id="g1", round_num=2, vote_num=3, history_path=history)
    line = json.loads(history.read_text(encoding="utf-8").splitlines()[0])
    assert line["round_num"] == 2
    assert line["vote_num"] == 3
    assert line["game_id"] == "g1"


def test_recent_questions_returns_last_30_only(tmp_path):
    history = tmp_path / "question_history.jsonl"
    for i in range(35):
        append_question(f"質問{i}", game_id="g1", round_num=1, vote_num=(i % 6) + 1, history_path=history)
    recent = recent_questions(history, n=HISTORY_SIZE)
    assert len(recent) == 30
    assert recent == [f"質問{i}" for i in range(5, 35)]


def test_recent_questions_missing_file_returns_empty(tmp_path):
    history = tmp_path / "does_not_exist.jsonl"
    assert recent_questions(history) == []


def test_recent_questions_reads_existing_v0_3_format_rows_without_vote_num(tmp_path):
    """
    既存のdata/question_history.jsonl（v0.3形式、vote_numの無い行）を読めること。
    既存の行は消さない・書き換えない（append_questionは常に追記のみ）。
    """
    history = tmp_path / "question_history.jsonl"
    v0_3_rows = [
        {"question": "v0.3の質問1", "game_id": "g_old", "round_num": 1,
         "timestamp": "2026-10-07T11:54:54.873486+00:00"},
        {"question": "v0.3の質問2", "game_id": "g_old", "round_num": 2,
         "timestamp": "2026-10-07T20:01:07.536254+00:00"},
    ]
    with history.open("w", encoding="utf-8") as f:
        for row in v0_3_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    before = history.read_text(encoding="utf-8")

    assert recent_questions(history) == ["v0.3の質問1", "v0.3の質問2"]

    append_question("v0.4の質問", game_id="g_new", round_num=1, vote_num=1, history_path=history)
    after_lines = history.read_text(encoding="utf-8").splitlines()
    assert after_lines[0] == before.splitlines()[0]  # 既存1行目は書き換えられていない
    assert after_lines[1] == before.splitlines()[1]  # 既存2行目も同様
    assert len(after_lines) == 3  # 追記された1行だけ増える
    assert recent_questions(history) == ["v0.3の質問1", "v0.3の質問2", "v0.4の質問"]


def test_append_question_creates_parent_dir(tmp_path):
    history = tmp_path / "nested" / "question_history.jsonl"
    append_question("質問X", game_id="g1", round_num=1, vote_num=1, history_path=history)
    assert history.exists()


# --- §12.3 #48: 試合で使った質問だけを履歴ファイルに追記する ---

def test_acceptance_48_only_used_9_questions_appended_to_history(tmp_path) -> None:
    """
    #48: 24問生成しても、試合で実際に使った（on_question_publishedが呼ばれた）
    質問だけを履歴ファイルに追記する。使わなかった分は書かない。

    append_question自体は「渡された1問を1行追記する」関数であり、「どれを
    渡すか」はGame.on_question_publishedフック（scripts/llm_trial.py側）の
    責務である。本テストはそのフックと同じ呼び方（使った質問だけを1問ずつ
    append_question）を再現して確認する。
    """
    history = tmp_path / "question_history.jsonl"
    adapter = FakeQuestionAdapter(text=_ai_text(VALID_24))
    result = generate_questions(adapter=adapter, history_path=history, seed=12)
    assert len(result.questions) == 24

    used_questions = result.questions[:9]  # 試合がR2V3あたりで打ち切られ、9問だけ使った想定
    for i, q in enumerate(used_questions):
        round_num, vote_num = divmod(i, 6)
        append_question(
            q, game_id="g_acc48", round_num=round_num + 1, vote_num=vote_num + 1,
            history_path=history,
        )

    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    written_questions = [json.loads(ln)["question"] for ln in lines]
    assert written_questions == used_questions
    for q in result.questions[9:]:
        assert q not in written_questions


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
        load_questions_file(f, expected=24)


def test_load_questions_file_default_expected_is_24(tmp_path):
    f = tmp_path / "questions.txt"
    f.write_text("\n".join(VALID_24), encoding="utf-8")
    assert load_questions_file(f) == VALID_24
