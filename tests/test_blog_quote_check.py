"""
引用・金額の機械照合（`blog/quote_check.py`）のテスト（サイクル6.0新設）
"""

from __future__ import annotations

from blog.quote_check import (
    check_article,
    extract_amounts_yen,
    extract_quotes,
    quote_matches,
    render_editor_note,
)

SOURCES = [
    "- 巡1の内心[感情:奸]: P09とP11をNOへ揃え、自分だけYESに入れて単独勝利を狙う。",
    "- 巡1 送ったDM→P02[#10]: 最小借入で無理な延長を避けたい点は一致しています。",
    "- 巡2 全体発言[#20]: この質問は素直に常識で判断してもいいと思います。",
]


def test_exact_match_found() -> None:
    assert quote_matches("P09とP11をNOへ揃え、自分だけYESに入れて単独勝利を狙う", SOURCES)


def test_nonexistent_quote_not_found() -> None:
    assert not quote_matches("私は絶対に裏切らない、信じてくれ", SOURCES)


def test_short_quote_under_min_length_not_extracted() -> None:
    """9字以下の引用は対象外（ゲーム用語「山分け」等の誤検出を避ける）。"""
    article = "本文中に「山分け」という言葉が出てくる。"
    assert extract_quotes(article) == []


def test_long_quote_over_min_length_extracted() -> None:
    article = "彼は「この質問は素直に常識で判断してもいいと思います」と言った。"
    quotes = extract_quotes(article)
    assert len(quotes) == 1
    assert quotes[0] == "この質問は素直に常識で判断してもいいと思います"


def test_ellipsis_quote_matches_when_parts_in_order_in_same_source() -> None:
    """「…」で省略された引用は、各部分が同じ1つの原文のどれかに順番どおり現れれば一致。"""
    quote = "P09とP11をNOへ揃え…単独勝利を狙う"
    assert quote_matches(quote, SOURCES)


def test_ellipsis_quote_does_not_match_across_different_sources() -> None:
    """2つの異なる原文をつなぎ合わせた偽の省略引用は一致しない。"""
    quote = "P09とP11をNOへ揃え…常識で判断してもいい"
    assert not quote_matches(quote, SOURCES)


def test_whitespace_and_width_normalization() -> None:
    """全角/半角・空白の違いだけなら一致とみなす。"""
    quote = "P０９とP１１をＮＯへ揃え、自分だけ ＹＥＳ に入れて単独勝利を狙う"
    assert quote_matches(quote, SOURCES)


def test_extract_amounts_comma_grouped() -> None:
    assert extract_amounts_yen("最終的に21,941,392円を得た。") == {21_941_392}


def test_extract_amounts_man_unit() -> None:
    assert extract_amounts_yen("参加費100万円を払った。") == {1_000_000}


def test_extract_amounts_man_unit_four_digits_no_comma() -> None:
    """4桁以上のカンマ無し万単位表記（"1320万円"）で先頭桁を取りこぼさないこと
    （実際の手記生成で"1320万円"が"320万円"と誤認識された実例の再発防止）。
    """
    assert extract_amounts_yen("山は1320万円だった。") == {13_200_000}


def test_extract_amounts_man_unit_comma_grouped() -> None:
    assert extract_amounts_yen("借入は1,000万円だった。") == {10_000_000}


def test_extract_amounts_raw_digits_only_with_include_raw() -> None:
    """素材の契約details（Pythonの辞書表現）に現れる"円"も","も無い生の数字
    （例: "{'amount': 2000000, ...}"）は、include_raw=Trueのときだけ拾う。
    """
    text = "内容{'amount': 2000000, 'condition_type': 'wins_round'}"
    assert extract_amounts_yen(text, include_raw=False) == set()
    assert extract_amounts_yen(text, include_raw=True) == {2_000_000}


def test_check_article_amount_in_material_as_raw_dict_not_flagged() -> None:
    """素材側に"amount': 2000000"のような生数字でしか現れない金額を、本文が
    "2,000,000円"と正しく表記した場合に誤って未一致扱いしないこと
    （実際の手記生成で発生した実例の再発防止）。
    """
    article = "条件付きで2,000,000円を受け取る約束だった。"
    material_text = "type_c_conditional（内容{'amount': 2000000, 'condition_type': 'wins_round'}）"
    result = check_article("P06", article, [], material_text)
    assert result.unmatched_amounts == []


def test_check_article_matched_and_unmatched_quotes() -> None:
    article = (
        "# タイトル\n"
        "彼の内心は「P09とP11をNOへ揃え、自分だけYESに入れて単独勝利を狙う」というものだった。\n"
        "しかし「私は絶対に裏切らない、信じてくれ」とは一度も言っていない記録にない創作だ。\n"
    )
    material_text = "\n".join(SOURCES)
    result = check_article("P06", article, SOURCES, material_text)
    assert result.matched_count == 1
    assert result.unmatched_quotes == ["私は絶対に裏切らない、信じてくれ"]


def test_check_article_amount_mismatch_recorded_not_dropped() -> None:
    """決定④: 一致しない金額は本文を書き換えず、照合結果に記録するだけ。"""
    article = "本当は99,999,999円も貰った。"
    material_text = "最終現金は21,941,392円だった。"
    result = check_article("P06", article, [], material_text)
    assert result.unmatched_amounts == [99_999_999]


def test_render_editor_note_empty_when_all_matched() -> None:
    from blog.quote_check import QuoteCheckResult

    result = QuoteCheckResult(pid="P01", matched_count=3)
    note = render_editor_note(result)
    assert "食い違う" in note
    assert "見つかりませんでした" in note


def test_render_editor_note_lists_unmatched_items() -> None:
    from blog.quote_check import QuoteCheckResult

    result = QuoteCheckResult(
        pid="P01", unmatched_quotes=["捏造された引用文です十文字以上"], unmatched_amounts=[99_999_999],
    )
    note = render_editor_note(result)
    assert "捏造された引用文です十文字以上" in note
    assert "99,999,999円" in note
