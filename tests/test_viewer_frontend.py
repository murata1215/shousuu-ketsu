"""
viewer/static/*.html・style.css の静的検証（サイクル3.0）

JSテスト基盤を新設しない方針（gentei-janken tests/test_viewer_frontend.pyと同じ
方針）のため、Pythonから静的にHTML/CSSを検査する。外部CDN不使用・ビューポート
指定・場面リンク関数の存在・参照している感情画像が実在することを確認する。
"""

import hashlib
import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "viewer" / "static"

# dangou-cardから移植したCSS本体（1〜559行目）のsha256。追記のみルールの担保
# （gentei-janken tests/test_viewer_frontend.pyと同じ方式）。
_STYLE_CSS_PREFIX_SHA256 = "32664c874897f3f7ee3feb0bb6ef0330ed9559ad19efd5828eb65dd81c658b44"
_STYLE_CSS_PREFIX_LINES = 559


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_style_css_dangou_prefix_unchanged() -> None:
    lines = _read("style.css").splitlines(keepends=True)
    prefix = "".join(lines[:_STYLE_CSS_PREFIX_LINES])
    assert hashlib.sha256(prefix.encode("utf-8")).hexdigest() == _STYLE_CSS_PREFIX_SHA256


def test_style_css_has_viewer3_additions_after_prefix() -> None:
    lines = _read("style.css").splitlines()
    assert len(lines) > _STYLE_CSS_PREFIX_LINES


def test_no_external_cdn_in_html() -> None:
    for name in ("index.html", "watch.html"):
        html = _read(name)
        assert "cdn." not in html
        assert "googleapis.com" not in html
        assert not re.search(r'<script[^>]+src="https?://', html)
        assert not re.search(r'<link[^>]+href="https?://', html)


def test_viewport_meta_present() -> None:
    for name in ("index.html", "watch.html"):
        html = _read(name)
        assert 'name="viewport"' in html
        assert "width=device-width" in html


def test_watch_html_has_scene_link_functions() -> None:
    html = _read("watch.html")
    for fn in ("parseSceneParams", "buildSceneUrl", "sceneFromCurrentState", "syncAddressBar", "copySceneLink", "highlightAndScroll"):
        assert f"function {fn}(" in html, f"missing {fn}"


def test_watch_html_has_scene_copy_buttons() -> None:
    html = _read("watch.html")
    assert "scene-copy-btn" in html
    assert html.count("copySceneLink(") >= 3  # 行動・契約提案・投票公開の最低3箇所


def test_watch_html_has_tabs_for_required_screens() -> None:
    html = _read("watch.html")
    for tab in ("overview", "round", "contracts", "seat"):
        assert f'data-tab="{tab}"' in html


def test_emotion_images_referenced_exist_on_disk() -> None:
    html = _read("watch.html")
    emotion_en = re.search(r"EMOTION_EN\s*=\s*\{([^}]+)\}", html)
    assert emotion_en
    ens = set(re.findall(r"'([a-z]+)'", emotion_en.group(1)))
    assert ens == {"joy", "anger", "sadness", "ease", "panic", "doubt", "smirk", "neutral"}
    for vendor in ("anthropic", "openai", "google", "xai", "moonshot", "deepseek"):
        for en in ens - {"neutral"}:
            assert (STATIC_DIR / "emotions" / f"{vendor}_{en}.png").exists(), f"{vendor}_{en}.png missing"


def test_emotion_images_count_matches_dangou_card_source() -> None:
    files = list((STATIC_DIR / "emotions").glob("*.png"))
    assert len(files) == 42  # 6ベンダ×7感情


def test_index_html_links_to_watch() -> None:
    html = _read("index.html")
    assert "watch?game=" in html


def test_watch_html_references_style_and_emotions_relative() -> None:
    html = _read("watch.html")
    assert 'href="static/style.css"' in html
    assert "static/emotions/" in html
