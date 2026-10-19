"""
手記アイキャッチ用「キャラカード」生成（サイクル6.0、`~/dangou-card/scripts/cards_seat.py`の
レイアウト設計・安全地帯の実測検査を少数決に合わせて作り直したもの）

出力 1200×630（OGP標準）。上下12%（y<76, y>554）を「安全地帯外」として、顔・席番号・
モデル名・順位バッジはすべて y=76〜554 の中央帯に収める（PixBlog一覧サムネの実測
2.1875:1中央クロップ・X/OGPの2:1中央クロップの両方をカバーする設計、dangou-card
サイクル10.18の実測に基づく）。

キャラ絵は`viewer/static/emotions/{vendor}_{emotion}.png`をbase64 data URIで埋め込む
（新規画像生成はしない）。表情の割り当ては`blog/seats.py::build_seat_table`が記録から
決定的に求める（手書きの表は持たない）。

レンダリングはPlaywright(Chromium)のみ（jinja2は使わない。HTMLは素のPython文字列
テンプレートで組み立てる。dangou-cardの`engine/blog/cards_html.py::CardRenderer`相当の
最小部分だけを自前で持つ）。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from blog.seats import SeatInfo

_EMOTIONS_DIR = Path(__file__).resolve().parent.parent / "viewer" / "static" / "emotions"

CARD_W, CARD_H = 1200, 630
SAFE_TOP, SAFE_BOTTOM = 76, 554  # 上下12%の安全地帯境界（y座標）
SAFE_RIGHT = 1160  # プレート右端より内側の左右安全マージン

LISTING_RATIO = 420 / 192  # PixBlog一覧枠の実測比率（dangou-cardサイクル10.18の実測値を踏襲）
OGP_RATIO = 2.0  # X / OGP summary_large_image

FOOTER = "嘘八百万 —少数決—　T12R4"


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _png_data_uri(path: Path, max_px: int | None = None) -> str | None:
    """PNGをbase64 data URI文字列にする。無ければNone。

    max_pxを渡すと、その長辺以下に縮小・再圧縮してから埋め込む（PILが使える前提。
    `pyproject.toml`にpillowを追加済み）。
    """
    if not path.exists():
        return None
    data = path.read_bytes()
    if max_px:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        if max(im.size) > max_px:
            im = im.convert("RGBA")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _name_font_px(name: str) -> int:
    """モデル名の文字数に応じてフォントサイズを段階的に縮小する（dangou-cardと同じ基準）。"""
    n = len(name)
    if n <= 16:
        return 62
    if n <= 19:
        return 54
    return 46


# ---------------------------------------------------------------------------
# Chromiumレンダラ（1回だけ起動して使い回す。jinja2は使わない）
# ---------------------------------------------------------------------------

class CardRenderer:
    """`with CardRenderer() as r: r.render_html(...)` で使う。Chromiumを1度だけ起動。"""

    def __init__(self, scale: int = 1):
        self.scale = scale
        self._pw = None
        self._browser = None

    def __enter__(self) -> "CardRenderer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=["--force-color-profile=srgb"])
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def render_html(self, html: str, width: int, height: int, out_path: str | Path, clip: str = ".card") -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page = self._browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=self.scale,
        )
        try:
            page.set_content(html, wait_until="networkidle")
            el = page.query_selector(clip)
            if el is None:
                raise RuntimeError(f"clip selector not found: {clip}")
            el.screenshot(path=str(out_path))
        finally:
            page.close()
        return out_path

    def measure(self, html: str, selectors: list[str]) -> dict[str, dict]:
        """主要要素のバウンディングボックス（CSSピクセル座標）を実測する。"""
        page = self._browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
        try:
            page.set_content(html, wait_until="networkidle")
            rects: dict[str, dict] = {}
            for sel in selectors:
                el = page.query_selector(sel)
                if el is None:
                    raise RuntimeError(f"要素が見つかりません: {sel}")
                box = el.bounding_box()
                if box is None:
                    raise RuntimeError(f"バウンディングボックスを取得できません: {sel}")
                rects[sel] = box
            return rects
        finally:
            page.close()


def assert_safe_zone(label: str, rects: dict[str, dict]) -> None:
    for sel, box in rects.items():
        top, bottom, right = box["y"], box["y"] + box["height"], box["x"] + box["width"]
        assert top >= SAFE_TOP, f"[{label}] {sel} が安全地帯上端を超えています: top={top:.1f} < {SAFE_TOP}"
        assert bottom <= SAFE_BOTTOM, f"[{label}] {sel} が安全地帯下端を超えています: bottom={bottom:.1f} > {SAFE_BOTTOM}"
        assert right <= SAFE_RIGHT, f"[{label}] {sel} が右安全地帯を超えています: right={right:.1f} > {SAFE_RIGHT}"


# ---------------------------------------------------------------------------
# 1席のカード
# ---------------------------------------------------------------------------

_CARD_CSS = """
:root {
  --gold: #d7b262;
  --gold-hi: #e9cd8a;
  --white: #e7ebf3;
  --subtext: #96969f;
  --edge: #3c3c48;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: __W__px; height: __H__px; background: #0b0d12; }
.card {
  position: relative;
  width: __W__px;
  height: __H__px;
  overflow: hidden;
  background:
    radial-gradient(120% 90% at 50% -10%, #23201d 0%, #17161a 48%, #0b0d12 100%);
  font-family: 'Noto Sans CJK JP', 'Noto Sans JP', 'Yu Gothic', sans-serif;
  color: var(--white);
}
.plate {
  position: absolute;
  inset: 22px;
  border: 2px solid var(--edge);
  border-radius: 26px;
  box-shadow: inset 0 0 0 1px rgba(215, 178, 98, 0.18), inset 0 0 120px rgba(0, 0, 0, 0.55);
}
.watermark {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%) rotate(-16deg);
  font-size: 220px;
  font-weight: 900;
  color: var(--gold);
  opacity: 0.07;
  white-space: nowrap;
  letter-spacing: 0.05em;
}
.seatno {
  position: absolute;
  left: 60px;
  top: 40px;
  font-size: 22px;
  letter-spacing: 0.18em;
  color: var(--subtext);
}
.footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 24px;
  text-align: center;
  font-size: 20px;
  letter-spacing: 0.08em;
  color: var(--subtext);
}
.safe {
  position: absolute;
  left: 0;
  right: 0;
  top: __SAFE_TOP__px;
  height: __SAFE_H__px;
  display: flex;
  align-items: center;
  padding: 0 70px;
  gap: 56px;
}
.face-wrap {
  flex: none;
  width: 400px;
  height: 400px;
  border-radius: 24px;
  background: #f5f5f5;
  box-shadow: 0 0 0 4px var(--gold), 0 14px 44px rgba(0, 0, 0, 0.55);
  overflow: hidden;
}
.face-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
.info { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; }
.pid-badge {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--subtext);
  margin-bottom: 10px;
}
.badge {
  display: inline-flex;
  border: 2px solid var(--gold);
  border-radius: 999px;
  color: var(--gold);
  letter-spacing: 0.14em;
  padding: 0.32em 1.1em;
  font-size: 24px;
  margin-bottom: 18px;
}
.name {
  font-weight: 900;
  color: var(--gold-hi);
  line-height: 1.08;
  margin-bottom: 10px;
}
.model-id {
  font-size: 22px;
  color: var(--subtext);
  margin-bottom: 16px;
  word-break: break-all;
}
.tier {
  font-size: 28px;
  color: var(--white);
}
"""

_CARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="card">
  <div class="plate"></div>
  <div class="watermark">少数決</div>
  <div class="seatno">SEAT / 席</div>
  <div class="safe">
    <div class="face-wrap"><img src="{face_uri}"></div>
    <div class="info">
      <div class="pid-badge">{pid}</div>
      <div class="badge">{badge}</div>
      <div class="name" style="font-size:{name_fs}px;">{name}</div>
      <div class="model-id">{model_id}</div>
      <div class="tier">{tier}</div>
    </div>
  </div>
  <div class="footer">{footer}</div>
</div>
</body></html>
"""

_SAFE_ZONE_SELECTORS = [".face-wrap", ".pid-badge", ".badge", ".name", ".model-id", ".tier"]


def build_card_html(seat: SeatInfo, footer: str = FOOTER) -> str:
    face_uri = _png_data_uri(_EMOTIONS_DIR / f"{seat.vendor}_{seat.emotion_en}.png", max_px=560)
    if not face_uri:
        raise SystemExit(f"[エラー] キャラ絵が見つかりません: {seat.vendor}_{seat.emotion_en}.png")
    css = (
        _CARD_CSS.replace("__W__", str(CARD_W)).replace("__H__", str(CARD_H))
        .replace("__SAFE_TOP__", str(SAFE_TOP)).replace("__SAFE_H__", str(SAFE_BOTTOM - SAFE_TOP))
    )
    badge = f"{seat.provider}" + ("（DevRelay実験席）" if seat.is_devrelay else "")
    html = _CARD_HTML.replace("{css}", css)
    for key, val in (
        ("{face_uri}", face_uri),
        ("{pid}", _esc(seat.pid)),
        ("{badge}", _esc(badge)),
        ("{name}", _esc(seat.display_name)),
        ("{name_fs}", str(_name_font_px(seat.display_name))),
        ("{model_id}", _esc(seat.short_label)),
        ("{tier}", _esc(f"最終 {seat.final_rank}位")),
        ("{footer}", _esc(footer)),
    ):
        html = html.replace(key, val, 1)
    return html


def render_card(seat: SeatInfo, renderer: CardRenderer, out_path: Path, footer: str = FOOTER) -> Path:
    html = build_card_html(seat, footer=footer)
    renderer.render_html(html, CARD_W, CARD_H, out_path, clip=".card")
    from PIL import Image

    im = Image.open(out_path)
    assert im.size == (CARD_W, CARD_H), f"サイズ不正: {im.size}"
    rects = renderer.measure(html, _SAFE_ZONE_SELECTORS)
    assert_safe_zone(seat.pid, rects)
    return out_path


# ---------------------------------------------------------------------------
# 総括カード: 12席の顔を2行×6列グリッドで並べた集合カード
# ---------------------------------------------------------------------------

_SUMMARY_EXTRA_CSS = """
.safe.summary { padding: 0 60px; gap: 40px; }
.grid {
  flex: none;
  width: 660px;
  height: 230px;
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 14px;
}
.tile {
  position: relative;
  border-radius: 14px;
  overflow: hidden;
  background: #f5f5f5;
  box-shadow: 0 0 0 3px var(--gold);
}
.tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tile .tag {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  font-size: 13px;
  font-weight: 700;
  text-align: center;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  padding: 3px 0;
  letter-spacing: 0.05em;
}
.summary-info { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; }
.summary-label {
  font-size: 24px;
  letter-spacing: 0.16em;
  color: var(--subtext);
  margin-bottom: 8px;
}
.summary-title {
  font-size: 52px;
  font-weight: 900;
  color: var(--gold-hi);
  line-height: 1.15;
  margin-bottom: 18px;
}
.summary-badge {
  display: inline-flex;
  border: 2px solid var(--gold);
  border-radius: 999px;
  color: var(--gold);
  letter-spacing: 0.14em;
  padding: 0.32em 1.1em;
  font-size: 24px;
  margin-bottom: 18px;
}
.summary-stat { font-size: 28px; color: var(--white); }
"""

_SUMMARY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="card">
  <div class="plate"></div>
  <div class="watermark">総括</div>
  <div class="seatno">RESULT / 総括</div>
  <div class="safe summary">
    <div class="grid">{tiles}</div>
    <div class="summary-info">
      <div class="summary-label">{label}</div>
      <div class="summary-title">{title}</div>
      <div class="summary-badge">{badge}</div>
      <div class="summary-stat">{stat}</div>
    </div>
  </div>
  <div class="footer">{footer}</div>
</div>
</body></html>
"""

_SUMMARY_SAFE_ZONE_SELECTORS = [".grid", ".summary-label", ".summary-title", ".summary-badge", ".summary-stat"]


def build_summary_card_html(
    seats_by_rank: list[SeatInfo], *, label: str, title: str, badge: str, stat: str, footer: str = FOOTER,
) -> str:
    """seats_by_rank（1位→最下位の順）の顔を2行×6列グリッドに並べる。"""
    css = (
        _CARD_CSS.replace("__W__", str(CARD_W)).replace("__H__", str(CARD_H))
        .replace("__SAFE_TOP__", str(SAFE_TOP)).replace("__SAFE_H__", str(SAFE_BOTTOM - SAFE_TOP))
    ) + _SUMMARY_EXTRA_CSS

    tiles_html: list[str] = []
    for seat in seats_by_rank:
        face_uri = _png_data_uri(_EMOTIONS_DIR / f"{seat.vendor}_{seat.emotion_en}.png", max_px=220)
        if not face_uri:
            raise SystemExit(f"[エラー] キャラ絵が見つかりません: {seat.vendor}_{seat.emotion_en}.png")
        tiles_html.append(f'<div class="tile"><img src="{face_uri}"><div class="tag">{_esc(seat.pid)}</div></div>')

    html = _SUMMARY_HTML.replace("{css}", css)
    for key, val in (
        ("{tiles}", "".join(tiles_html)),
        ("{label}", _esc(label)),
        ("{title}", _esc(title)),
        ("{badge}", _esc(badge)),
        ("{stat}", _esc(stat)),
        ("{footer}", _esc(footer)),
    ):
        html = html.replace(key, val, 1)
    return html


def render_summary_card(
    seats_by_rank: list[SeatInfo], renderer: CardRenderer, out_path: Path,
    *, label: str, title: str, badge: str, stat: str, footer: str = FOOTER,
) -> Path:
    html = build_summary_card_html(seats_by_rank, label=label, title=title, badge=badge, stat=stat, footer=footer)
    renderer.render_html(html, CARD_W, CARD_H, out_path, clip=".card")
    from PIL import Image

    im = Image.open(out_path)
    assert im.size == (CARD_W, CARD_H), f"サイズ不正: {im.size}"
    rects = renderer.measure(html, _SUMMARY_SAFE_ZONE_SELECTORS)
    assert_safe_zone("summary", rects)
    return out_path
