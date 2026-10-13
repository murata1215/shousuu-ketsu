"""
ビューアが扱うイベント種別の全数検査（サイクル4.3新設）

CLAUDE.md過去の落とし穴⑩（「公開と記録した」のに届かない構造）と同型の
再発防止: `engine/`側が発行する全イベント種別
（`tests/test_event_visibility.py::FIXED_VISIBILITY`で固定済み）が、
ビューア側の対応表（`viewer/log_parser.py::KNOWN_EVENT_TYPES`）に
すべて載っていることを機械的に確認する。新しいイベント種別をengine側に
足したら、ビューア側にも対応を追加しないとこのテストが落ちる。
"""

from tests.test_event_visibility import FIXED_VISIBILITY
from viewer.log_parser import KNOWN_EVENT_TYPES


def test_all_engine_event_types_are_known_to_viewer() -> None:
    missing = set(FIXED_VISIBILITY) - KNOWN_EVENT_TYPES
    assert not missing, f"engineが発行するがビューアが知らないイベント種別: {missing}"


def test_known_event_types_includes_negotiation_action() -> None:
    """NEGOTIATION_ACTIONはdata['action']で公開区分が変わる特別枠（§8）だが、
    ビューアの対応表には種別として載っていること"""
    assert "NEGOTIATION_ACTION" in KNOWN_EVENT_TYPES
