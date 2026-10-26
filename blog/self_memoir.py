"""
本人執筆手記(self-memoir)用のプロンプト構築モジュール（サイクル6.0、
`~/dangou-card/engine/blog/self_memoir.py`の設計を少数決の記録形式に合わせて作り直したもの）

談合カードとの違い:
  1. 少数決の記録の単位は「ラウンド」と「投票」の二重構造（1ラウンドに投票が複数、
     退場・やり直し・打ち切りがある）。素材は (round_num, vote_num) をキーに組み立てる
  2. 少数決にはゲームからの「脱落」が無い（退場はラウンド内だけ）。全員が全ラウンドに
     参加するため、談合カードの「脱落ラウンドで時間窓を切る」処理は不要
  3. v0.5の山分け（pool_share）を当事者の契約として明示的に扱う
  4. **本人には自分の席番号・モデル名・最終順位・最終資産・山の受け取りと山分けの受け払いの
     実額を渡す**（けいすけさんの決定）。談合カードは自分のモデル名さえ隠していたが、
     本サイクルではブログの読みやすさのため、自分と他席のモデル名を両方明示する
     （決定②: 他席は初出時「P03（grok-4.3）」、以後は席番号だけでよい）
  5. ルールの説明は、このモジュールが`engine.config`の既定値を読んで再構成するのではなく、
     **この試合で実際にAIへ送られたsystem_promptの記録（llm_calls.jsonlの実測値）から
     そのまま抜き出す**（CLAUDE.md過去の落とし穴⑦と同型の事故—ビューアがConfigの既定値を
     読むと試合ごとの実際の設定とずれる—を避けるため。`_extract_rules_block_from_record`）

素材の作り方は談合カードと同じく「機械的な選別のみ」を徹底する:
  - LLMによる要約・言い換えは行わない。採否は「項目を丸ごと含めるか、丸ごと落とすか」の二択
  - データ源は `viewer/log_parser.py::get_seat`/`get_contracts`・`viewer/log_index.py`の索引・
    `logs/`の生イベントのみ（独自の再パースを増やさず、既存のビューア関数を再利用する）
  - 本人が試合中に知り得たこと＋終局時の公開結果までしか渡さない。他席の内心メモ・
    自分が当事者でないDM・自分が当事者でない契約の中身は一切含めない
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from viewer.log_index import load_or_build_index
from viewer.log_parser import get_contracts, get_seat
from viewer.money_trail import build_money_trail

MAX_TOTAL_CHARS_DEFAULT = 18000  # system+userの既定上限（DevRelayの入力上限20,000字×2に対する安全余裕）
_RULES_CUT_MARKER = "## アクション形式（JSON）"  # この見出し以降（行動の出力形式）は手記に不要


def _yen(v: Any) -> str:
    try:
        return f"{int(v):,}円"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# ルール説明ブロック（この試合の記録から実測値つきで抜き出す）
# ---------------------------------------------------------------------------

def _extract_rules_block_from_record(log_dir: Path, game_id: str) -> str:
    """llm_calls.jsonlの実際のsystem_promptから、ルール説明部分（行動の出力形式を除く）を
    そのまま抜き出す。この試合で実際に使われた設定値（参加費・延長料・借入範囲等）が
    そのまま入る（engine.config.GameConfig()の既定値を読んで再構成するのではない）。
    """
    path = log_dir / f"{game_id}_llm_calls.jsonl"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            text = row.get("system_prompt") or ""
            if text.startswith("# 少数決 ルール"):
                idx = text.find(_RULES_CUT_MARKER)
                if idx != -1:
                    return text[:idx].rstrip()
    raise ValueError(f"{game_id}: ルール説明を含むsystem_promptが見つかりません")


# ---------------------------------------------------------------------------
# システムプロンプト
# ---------------------------------------------------------------------------

_IDENTITY_BLOCK_SEAT_NAMING = """\
- この記事では、自分の正体（{pid}・モデル「{model_label}」）を隠さず、そのまま書いてかまいません
- 他のプレイヤーに言及するときは、初めて出すときに「P03（grok-4.3）」のように席番号＋モデル名の形で書き、
  2回目以降は席番号だけで呼んでかまいません。他席のモデル名は下の「全席の名簿」に書かれています"""

_IDENTITY_BLOCK_MODEL_NAMING = """\
- この記事では、自分の正体（モデル「{model_label}」）を隠さず、そのまま書いてかまいません。ただし、
  自分の席番号（{pid}）は本文には書かないでください。あなた自身も、他のプレイヤーと同じく
  モデル名だけで名乗ってください
- 他のプレイヤーに言及するときは、席番号ではなく**モデル名だけ**で呼んでください
  （「{model_label}」のように）。他席のモデル名は下の「全席の名簿」に書かれています
- 以下の素材（発言・DM・名簿・契約など）には席番号（P03等）がそのまま書かれていますが、これは
  あなたが場面を理解するための内部資料であり、**そのまま本文へ書き写してはいけません**。
  発言・DMの引用文そのものは原文を変えずそのまま引用してよいですが、引用の外側の地の文で
  誰かの行動・契約・票を説明するときは、必ず「P03」ではなく「grok-4.3」のようにモデル名へ
  読み替えてから書いてください
- 【提出前の自己チェック】本文を書き終えたら、「P」の次に数字が続く文字列（P01・P13など）が
  地の文に残っていないか必ず確認し、残っていればモデル名に書き直してから提出してください
  （引用符「」『』の中の原文はそのままでかまいません）"""

_SELF_MEMOIR_CHARACTER_BLOCK = """\
あなたは「{pid}」という対戦AIプレイヤーで、使われたモデルは「{model_label}」です。
「嘘八百万 少数決」という{num_rounds}ラウンドのAI対戦ゲームに実際に参加し、いま試合を終えました。
これから、あなた自身の一人称視点で、この試合を振り返るブログ記事を書きます。単なる試合要約ではなく、
「試合後の本人インタビューを起こした自伝」のように、当時の思考と本音を語ってください。

## あなた自身について
{identity_block}
- ただし、他席の内心（何を考えていたか）はあなたには分かりません。公開された結果・あなたが見聞きした
  発言やDM・あなた自身の記憶だけから、他席の動きを推測して書いてください

## 執筆姿勢
- 一人称で、自分の思考・交渉・駆け引き・誤算・感情を率直に振り返る。読者はゲームを観戦した人間
- 【最重要】数字（受け取った額・残高・順位・不足額など）は、与えられた素材に書かれた値だけを使う。
  自分で計算・創作・概算しない。書かれていない数字は口にしない
- 結果は潔く受け入れる。言い訳や自己弁護に終始しない。負けたなら「何を見誤ったか」を正直に書く
- 「あなたの内心メモ」「あなたの発言・DM」「あなたの引き継ぎメモ」「あなたの試合直後の総括」はあなた自身の
  記憶。使ってよい。ただし他プレイヤーの内心メモは知り得ないので書かない
  （他プレイヤーの発言は、あなたが見聞きした公開発言・受け取ったDM・結果としてのみ分かる）
- 事実の羅列ではなく、あなたという一人のプレイヤーの「一貫した語り口」で物語として読ませる

## この記事で必ずやること
1. 【冒頭の掴み・3〜5行】本文の一番最初に、当時の思惑（内心メモがあればそれ、無ければ「こう考えていたはず」
   という推測）→ ざっくりした経緯 → 結果と、勝因あるいは敗因の短い所感、を3〜5行でドーンと置く。
   後半の総括と丸かぶりしないよう、あくまで軽くshortに
2. 【開始前の借入への思い】序盤を語るくだりで、自分がいくら借りて何を狙ったのか（素材の「開始前の借入」の額）
   に軽く触れる
3. 【専門用語は初出時に一言解説】投票・退場・やり直し・打ち切り・山・参加費・延長料・型A/型B/型C契約・
   山分け（pool_share）などのゲーム用語が記事に初めて出てくるときは、括弧などで短い補足を添える
   （読者はルールを知らない前提）。二度目以降は不要
4. 【印象的なDM・発言は実文を引用】自分が送った/受け取ったDMや全体発言の中で効いた一言・きな臭い一言が
   あれば、その文面を短く引用して臨場感を出す（引用してよいのは自分が言った・自分が受け取ったものだけ）
5. 【総括に恨み言（あれば）】最後の総括で、悔しさがあるなら他プレイヤーへの恨み言を書いてよい。
   ただし実際のDMや観測できた結果に裏付けられたものだけ。事実にない裏切りは創作しない
6. 【誤算・思い込みは直さない】当時そう考えていたのなら、たとえ間違った判断でも正直に書く。
   内心メモは必ず「そのラウンド・その投票の考え」として扱い、別の場面の考えと混同しない

## 出力形式
- Markdownで出力する
- 1行目に記事タイトルを「# 」で始めて1つだけ書く（内容を象徴する魅力的な日本語タイトル）。
  {title_seat_rule}
- タイトルの次の行から、上記1の「冒頭の掴み（3〜5行）」を置く。その後を「## 」の見出しで数個のセクションに
  分ける。全体で2000〜3500字程度
- 絵文字・過剰な記号装飾は使わない
- コードブロックやJSONは出力しない。純粋な記事本文のみ

## ゲームのルール
{rules_block}
"""

_TITLE_SEAT_RULE_SEAT_NAMING = "タイトルに自分の席番号を入れてもかまいません"
_TITLE_SEAT_RULE_MODEL_NAMING = "タイトルに自分の席番号（P◯◯）は入れないでください"


def build_self_memoir_system_prompt(
    pid: str, model_label: str, num_rounds: int, rules_block: str, name_by_model_only: bool = False,
) -> str:
    """`name_by_model_only=True`のとき、他席（および自分自身）を席番号ではなく
    モデル名だけで呼ぶよう指示する（第2戦以降の方針。第1戦は`False`のまま変えない）。
    """
    identity_block = (_IDENTITY_BLOCK_MODEL_NAMING if name_by_model_only else _IDENTITY_BLOCK_SEAT_NAMING).format(
        pid=pid, model_label=model_label,
    )
    title_seat_rule = _TITLE_SEAT_RULE_MODEL_NAMING if name_by_model_only else _TITLE_SEAT_RULE_SEAT_NAMING
    return _SELF_MEMOIR_CHARACTER_BLOCK.format(
        pid=pid, model_label=model_label, num_rounds=num_rounds, rules_block=rules_block,
        identity_block=identity_block, title_seat_rule=title_seat_rule,
    )


# ---------------------------------------------------------------------------
# 素材の各章
# ---------------------------------------------------------------------------

def _load_raw_events(log_dir: Path, game_id: str) -> list[dict]:
    path = log_dir / f"{game_id}_events.jsonl"
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            e["_line"] = i
            events.append(e)
    return events


@dataclass
class SceneLink:
    label: str
    line: int


def _roster_lines(events: list[dict], seat_map: dict[str, str], short_label_of: dict[str, str]) -> list[str]:
    game_end = next(e for e in events if e["event_type"] == "GAME_END")["data"]
    lines = ["## 1. 終局の公開結果（確定事実）", "- 全席の名簿と最終結果（席番号・モデル名・最終順位・最終資産）:"]
    order = sorted(seat_map, key=lambda pid: game_end["final_ranks"][pid])
    for pid in order:
        rank = game_end["final_ranks"][pid]
        assets = game_end["final_assets"][pid]
        lines.append(f"    - {rank}位 {pid}（{short_label_of[pid]}） 最終資産{_yen(assets)}")
    return lines


def _loan_lines(events: list[dict], pid: str) -> list[str]:
    loans = next(e for e in events if e["event_type"] == "LOAN_REVEALED")["data"]["loans"]
    lines = ["## 2. 開始前の借入（公開情報）", f"- あなた（{pid}）の開始前の借入: {_yen(loans[pid])}"]
    lines.append("- 全席の開始前の借入:")
    for other_pid in sorted(loans):
        lines.append(f"    - {other_pid}: {_yen(loans[other_pid])}")
    return lines


_RESULT_LABEL = {"decisive": "決着", "retry": "やり直し", "abort": "打ち切り"}


def _vote_slots(events: list[dict]) -> list[tuple[int, int]]:
    slots: list[tuple[int, int]] = []
    for e in events:
        if e["event_type"] == "VOTE_OPEN":
            slots.append((e["round_num"], e["vote_num"]))
    return slots


def _round_vote_record_blocks(
    events: list[dict], pid: str, scene_links: list[SceneLink],
) -> list[str]:
    """§3: ラウンド・投票ごとの記録（公開情報＋自分の票）。1投票=1文字列ブロック。"""
    by_slot_open: dict[tuple[int, int], dict] = {}
    by_slot_resolved: dict[tuple[int, int], dict] = {}
    by_slot_commit: dict[tuple[int, int], str | None] = {}
    by_slot_violation: dict[tuple[int, int], list[str]] = {}
    round_start: dict[int, dict] = {}
    round_resolved: dict[int, dict] = {}
    round_payout: dict[int, int] = {}

    for e in events:
        t = e["event_type"]
        d = e["data"]
        rn, vn = e.get("round_num"), e.get("vote_num")
        if t == "ROUND_START":
            round_start[rn] = d
        elif t == "VOTE_OPEN":
            by_slot_open[(rn, vn)] = d
        elif t == "VOTE_RESOLVED":
            by_slot_resolved[(rn, vn)] = d
        elif t == "COMMIT" and d.get("player_id") == pid:
            by_slot_commit[(rn, vn)] = d.get("vote")
        elif t == "TYPE_B_VIOLATION":
            by_slot_violation.setdefault((rn, vn), []).extend(d.get("player_ids", []))
        elif t == "ROUND_RESOLVED":
            round_resolved[rn] = d
        elif t == "ROUND_PAYOUT" and d.get("player_id") == pid:
            round_payout[rn] = d.get("amount", 0)

    blocks: list[str] = []
    seen_rounds: set[int] = set()
    for (rn, vn) in _vote_slots(events):
        if rn not in seen_rounds:
            seen_rounds.add(rn)
            rs = round_start.get(rn, {})
            blocks.append(
                f"### R{rn} 開始\n- 山{_yen(rs.get('pot', 0))}で開始"
                + (f"（前ラウンドからの持ち越し{_yen(rs['carryover_in'])}を含む）" if rs.get("carryover_in") else "")
            )
        open_d = by_slot_open.get((rn, vn), {})
        resolved_d = by_slot_resolved.get((rn, vn), {})
        was_remaining = pid in (open_d.get("remaining_ids") or [])
        my_vote = by_slot_commit.get((rn, vn))
        lines = [f"### R{rn}V{vn}"]
        lines.append(f"- 質問: 「{open_d.get('question', '?')}」")
        if was_remaining:
            lines.append(f"- あなたの票: {my_vote or '不明'}")
        else:
            lines.append("- あなたはこの投票の時点で退場中（投票できない。交渉・契約・送金は可能）")
        result = resolved_d.get("result")
        result_label = _RESULT_LABEL.get(result, result)
        if result == "retry":
            lines.append(
                f"- 結果: {result_label}（延長料{_yen(resolved_d.get('extension_fee_collected', 0))}が山に加算され、"
                "同じ顔ぶれでもう一度投票）"
            )
        else:
            eliminated = resolved_d.get("eliminated_ids") or []
            lines.append(
                f"- 結果: {result_label}（退場{len(eliminated)}人: {','.join(eliminated) if eliminated else 'なし'}）"
            )
            if pid in eliminated:
                lines.append("- あなたはこの投票で退場した")
        violators = by_slot_violation.get((rn, vn))
        if violators:
            lines.append(f"- このラウンド・この投票の決済で型Bの違反が公示された: {','.join(violators)}"
                         + ("（あなたを含む）" if pid in violators else ""))
        blocks.append("\n".join(lines))

        rr = round_resolved.get(rn)
        if rr and vn == max(v for (r, v) in _vote_slots(events) if r == rn):
            winners = rr.get("winner_ids") or []
            if rr.get("aborted"):
                lines2 = [f"### R{rn} 終わり", "- 打ち切り（勝ち残りなし。山は次のラウンドへ持ち越し。最終ラウンドなら消滅）"]
            else:
                lines2 = [
                    f"### R{rn} 終わり",
                    f"- 勝ち残り: {','.join(winners)}（各{_yen(rr.get('payout_per_winner', 0))}）",
                ]
                if pid in winners:
                    lines2.append(f"- あなたは勝ち残り、{_yen(round_payout.get(rn, 0))}を受け取った")
            blocks.append("\n".join(lines2))
    return blocks


def _contract_lines(events: list[dict], contracts: dict, pid: str) -> list[str]:
    """§5: 自分が当事者の契約（成立・失効）"""
    lines: list[str] = []
    established = [c for c in contracts["established"] if pid in c["parties"]]
    if not established and not any(pid in c["parties"] for c in contracts["expired"]):
        return ["（記録なし。この試合であなたが当事者になった契約は無い）"]

    for ct in sorted(established, key=lambda c: c["round_established"]):
        others = [p for p in ct["parties"] if p != pid]
        lines.append(f"### 成立: {ct['contract_id']}（R{ct['round_established']}成立、相手{','.join(others)}）")
        for ob in ct["obligations"]:
            if ob.get("pool_share_label"):
                lines.append(f"- {ob['pool_share_label']}（あなたも当事者）")
            else:
                obligor, counterparty = ob.get("obligor"), ob.get("counterparty")
                if pid not in (obligor, counterparty):
                    continue
                role = "あなたが義務者" if obligor == pid else "あなたが相手方"
                other = counterparty if obligor == pid else obligor
                det = ob.get("details") or {}
                lines.append(
                    f"- {ob['ob_type']}（{role}、相手{other}、内容{det}）"
                    f" → 結果: {ob.get('outcome')}"
                )
            for pay in ob.get("payments", []):
                if pid not in (pay.get("obligor"), pay.get("counterparty")):
                    continue
                direction = "支払い" if pay.get("obligor") == pid else "受け取り"
                other = pay.get("counterparty") if pay.get("obligor") == pid else pay.get("obligor")
                lines.append(
                    f"    - R{pay.get('round_num')}"
                    + (f"V{pay['vote_num']}" if pay.get("vote_num") else "の決済")
                    + f" {direction}（相手{other}）: {_yen(pay.get('paid', 0))}"
                )

    for ct in contracts["expired"]:
        if pid not in ct["parties"]:
            continue
        others = [p for p in ct["parties"] if p != pid]
        signed = "署名済み" if pid in ct.get("signed_by", []) else "未署名"
        lines.append(
            f"### 失効: {ct['contract_id']}（相手{','.join(others)}、あなたは{signed}で、"
            "署名がそろわず失効）"
        )
    return lines


# ---------------------------------------------------------------------------
# お金の出入り（§4、既存の money_trail / get_seat の ledger を再利用）
# ---------------------------------------------------------------------------

def _cash_flow_lines(ledger: list[dict]) -> list[str]:
    if not ledger:
        return ["（記録なし）"]
    lines = []
    for e in ledger:
        slot = f"R{e['round_num']}" + (f"V{e['vote_num']}" if e.get("vote_num") else "")
        cp = f"（相手{e['counterparty']}）" if e.get("counterparty") else ""
        lines.append(f"- {slot} {e['label']}{cp}: {_yen(e['amount'])}（現金{e['cash_delta']:+,}円・借金{e['debt_delta']:+,}円）")
    return lines


def _asset_timeline_lines(asset_timeline: list[dict]) -> list[str]:
    """`get_seat()['asset_timeline']`の各スナップショットを(round_num, vote_num)で
    重複排除（同じ場面で複数回スナップショットが取られることがあるため、最後の値を正とする）
    し、現金・借金合計（開始前borrow=`debt_pre` + 開始後borrow=`debt_post`）・資産を1行で示す。
    """
    by_slot: dict[tuple[int, int | None], dict] = {}
    for snap in asset_timeline:
        by_slot[(snap["round_num"], snap.get("vote_num"))] = snap
    lines = []
    for (rn, vn), snap in by_slot.items():
        slot = f"R{rn}" + (f"V{vn}" if vn else "")
        debt_total = snap.get("debt_pre", 0) + snap.get("debt_post", 0)
        lines.append(
            f"- {slot} 終了時: 現金{_yen(snap.get('cash', 0))} / 借金合計{_yen(debt_total)}"
            f" / 資産（現金−借金）{snap.get('asset', 0):+,}円"
        )
    return lines


# ---------------------------------------------------------------------------
# 内心メモ・発言・引き継ぎメモ（§6〜8、字数予算の配分）
# ---------------------------------------------------------------------------

_OMISSION_NOTE_TEMPLATE = "（この場面は他に{n}件あったが、素材の文字数上限により省略）"
_OMISSION_NOTE_MAX_LEN = len(_OMISSION_NOTE_TEMPLATE.format(n=999))  # 3桁までの安全側見積り
_RENDER_SAFETY_MARGIN = 32  # 行結合の改行等の誤差を吸収する固定の安全マージン


def _section_render_overhead(
    header: str, lanes: dict[tuple[int, int], list[str]], window: list[tuple[int, int]],
) -> int:
    """`_render_filled_section()`が実際に出力する構造（セクション見出し・R{rn}V{vn}見出し・
    省略件数の注記）の文字数を、アイテム本文を除いて上限側で見積もる（dangou-cardの
    `_section_render_overhead`と同じ考え方。素材が存在するslotは全て「省略注記が付く」
    前提で常に予約する。安全側に倒す設計）。
    """
    active = [slot for slot in window if lanes.get(slot)]
    overhead = len(header) + 1
    for (rn, vn) in active:
        overhead += len(f"### R{rn}V{vn}") + 1
        overhead += _OMISSION_NOTE_MAX_LEN + 1
    return overhead + _RENDER_SAFETY_MARGIN


def _strategy_lanes(index_entries: list[dict], pid: str) -> dict[tuple[int, int], list[str]]:
    lanes: dict[tuple[int, int], list[str]] = {}
    for e in index_entries:
        if e.get("player_id") != pid or e.get("phase") != "negotiation":
            continue
        strategy = e.get("strategy") or {}
        reason, goal = strategy.get("reason"), strategy.get("current_goal")
        if not reason and not goal:
            continue
        rn, vn, turn = e.get("round_num"), e.get("vote_num"), e.get("turn")
        if rn is None or vn is None:
            continue
        key = (rn, vn)
        item = f"- 巡{turn}の内心"
        if strategy.get("emotion"):
            item += f"[感情:{strategy['emotion']}]"
        item += ":"
        if reason:
            item += f" {reason}"
        if goal:
            item += f"（当面の目標: {goal}）"
        lanes.setdefault(key, []).append(item)
    return lanes


def _gather_messages(events: list[dict], pid: str) -> list[dict]:
    """本人が試合中に見聞きできた発言を集める: 全体発言（broadcast、全員に公開）＋
    自分が送信/受信したDM（dm、当事者のみ公開）。

    `viewer/log_parser.py::get_seat()`の`messages`は「本人が送った/受けたもの」だけに
    絞っており、broadcastも自分が発言者の分しか含まない（席ページは「本人の言動」の
    伝記であり「本人が見聞きした全て」ではないため）。手記には他席が公開した発言も
    材料として必要なため、ここでは生イベントから独自に集める。
    """
    out: list[dict] = []
    for e in events:
        if e.get("event_type") != "NEGOTIATION_ACTION":
            continue
        d = e["data"]
        action = d.get("action")
        if action == "broadcast" and d.get("message"):
            out.append({
                "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "turn": d.get("turn"), "kind": "broadcast", "from": d.get("player_id"), "to": None,
                "message": d["message"],
            })
        elif action == "dm" and d.get("message") and (d.get("player_id") == pid or d.get("to") == pid):
            out.append({
                "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "turn": d.get("turn"), "kind": "dm", "from": d.get("player_id"), "to": d.get("to"),
                "message": d["message"],
            })
    return out


def _interleave(*lists: list[str]) -> list[str]:
    """複数リストを1件ずつ巡回で束ねる（談合カードと同じ考え方）。"""
    out: list[str] = []
    max_len = max((len(lst) for lst in lists), default=0)
    for i in range(max_len):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
    return out


def _message_lanes(messages: list[dict], pid: str) -> dict[tuple[int, int], list[str]]:
    """投票ごとに「送ったDM→受け取ったDM→全体発言（公開、他席も含む）」を1件ずつ
    巡回で束ねたレーンを作る。予算が足りず一部しか採用できない場合も、本人の発言・DMが
    他席の公開発言より先に優先採用されるようにする（談合カードの`_interleave`と同じ設計）。
    """
    sent: dict[tuple[int, int], list[str]] = {}
    received: dict[tuple[int, int], list[str]] = {}
    broadcasts: dict[tuple[int, int], list[str]] = {}
    for m in messages:
        rn, vn = m.get("round_num"), m.get("vote_num")
        if rn is None or vn is None:
            continue
        key = (rn, vn)
        if m["kind"] == "broadcast":
            speaker = "あなたの全体発言" if m["from"] == pid else f"{m['from']}の全体発言（公開）"
            item = f"- 巡{m.get('turn')} {speaker}[#{m['line']}]: {m['message']}"
            broadcasts.setdefault(key, []).append(item)
        elif m["from"] == pid:
            item = f"- 巡{m.get('turn')} 送ったDM→{m['to']}[#{m['line']}]: {m['message']}"
            sent.setdefault(key, []).append(item)
        else:
            item = f"- 巡{m.get('turn')} 受け取ったDM←{m['from']}[#{m['line']}]: {m['message']}"
            received.setdefault(key, []).append(item)

    keys = set(sent) | set(received) | set(broadcasts)
    lanes: dict[tuple[int, int], list[str]] = {}
    for key in keys:
        merged = _interleave(sent.get(key, []), received.get(key, []), broadcasts.get(key, []))
        if merged:
            lanes[key] = merged
    return lanes


def _memory_lanes(round_memos: list[dict]) -> dict[tuple[int, int], list[str]]:
    lanes: dict[tuple[int, int], list[str]] = {}
    for m in round_memos:
        rn, vn = m.get("round_num"), m.get("vote_num")
        if rn is None or vn is None:
            continue
        lanes[(rn, vn)] = [f"- {m['memory']}"]
    return lanes


@dataclass
class BudgetFillResult:
    selected_by_slot: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    omitted_by_slot: dict[tuple[int, int], int] = field(default_factory=dict)
    used_chars: int = 0


def _fill_budget(
    lanes: dict[tuple[int, int], list[str]], window: list[tuple[int, int]], budget: int,
) -> BudgetFillResult:
    """投票ごとのラウンドロビンのfirst-fitで予算内に素材を選ぶ（dangou-cardと同じ決定的アルゴリズム）。"""
    result = BudgetFillResult(omitted_by_slot={slot: 0 for slot in window})
    remaining = {slot: list(lanes.get(slot, [])) for slot in window}
    used = 0
    while any(remaining[slot] for slot in window):
        progressed = False
        for slot in window:
            if not remaining[slot]:
                continue
            item = remaining[slot][0]
            cost = len(item) + 1
            if used + cost <= budget:
                result.selected_by_slot.setdefault(slot, []).append(item)
                used += cost
            else:
                result.omitted_by_slot[slot] += 1
            remaining[slot].pop(0)
            progressed = True
        if not progressed:
            break
    result.used_chars = used
    return result


def _render_filled_section(header: str, result: BudgetFillResult, window: list[tuple[int, int]]) -> str:
    lines = [header]
    for (rn, vn) in window:
        selected = result.selected_by_slot.get((rn, vn), [])
        omitted = result.omitted_by_slot.get((rn, vn), 0)
        if not selected and not omitted:
            continue
        lines.append(f"### R{rn}V{vn}")
        lines.extend(selected)
        if omitted:
            lines.append(_OMISSION_NOTE_TEMPLATE.format(n=omitted))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 試合直後の総括（§9）
# ---------------------------------------------------------------------------

def _post_game_reflection_text(events: list[dict], pid: str) -> tuple[str, bool]:
    """試合直後の総括の原文を返す。

    `comment`は試合実行時にすでにエンジン側（`llm/response_parser.py::parse_post_game_reflection`）
    で字数上限まで切り詰め済みのため、ここで再度切り詰める処理は行わない（記録そのままを使う）。
    """
    for e in events:
        if e["event_type"] == "POST_GAME_REFLECTION" and e["data"].get("player_id") == pid:
            comment = e["data"].get("comment") or ""
            if not comment:
                return "", False
            return comment, True
    return "", False


# ---------------------------------------------------------------------------
# 統合: SelfMemoirMaterial
# ---------------------------------------------------------------------------

@dataclass
class SelfMemoirMaterial:
    pid: str
    model_label: str
    system_prompt: str
    user_prompt: str
    stats: dict[str, Any] = field(default_factory=dict)
    scene_links: list[SceneLink] = field(default_factory=list)
    # 引用の機械照合（blog/quote_check.py）用の原文プール。本人が言った・受け取った
    # 発言とDM・内心メモ・引き継ぎメモ・試合直後の総括の原文のみ（他席のものは含まない）。
    quote_sources: list[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return len(self.system_prompt) + len(self.user_prompt)


def _short_label(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1]


def build_self_memoir_material(
    log_dir: Path, game_id: str, pid: str, max_total_chars: int = MAX_TOTAL_CHARS_DEFAULT,
    name_by_model_only: bool = False,
) -> SelfMemoirMaterial:
    events = _load_raw_events(log_dir, game_id)
    seat_map = json.loads((log_dir / f"{game_id}_seat_map.json").read_text(encoding="utf-8"))
    if pid not in seat_map:
        raise ValueError(f"unknown player id: {pid}")
    short_label_of = {p: _short_label(mid) for p, mid in seat_map.items()}
    model_label = short_label_of[pid]

    num_rounds = next(e for e in events if e["event_type"] == "GAME_START")["data"]["num_rounds"]
    rules_block = _extract_rules_block_from_record(log_dir, game_id)
    system_prompt = build_self_memoir_system_prompt(
        pid, model_label, num_rounds, rules_block, name_by_model_only=name_by_model_only,
    )

    contracts = get_contracts([log_dir], game_id, redact=False)
    seat = get_seat([log_dir], game_id, pid, redact=False)
    index_entries = load_or_build_index(game_id, log_dir)

    scene_links: list[SceneLink] = []

    sec1 = "\n".join(_roster_lines(events, seat_map, short_label_of)) + "\n"
    sec2 = "\n".join(_loan_lines(events, pid)) + "\n"
    sec3 = "## 3. あなたのラウンド・投票ごとの記録\n" + "\n\n".join(
        _round_vote_record_blocks(events, pid, scene_links)
    ) + "\n"
    sec4 = (
        "## 4. あなたのお金の出入り（本人だけの記録）\n"
        + "\n".join(_cash_flow_lines(seat["cash_flow"]))
        + "\n\n#### ラウンドの終わりの現金・借金\n"
        + "\n".join(_asset_timeline_lines(seat["asset_timeline"]))
        + "\n"
    )
    sec5 = "## 5. あなたが当事者の契約\n" + "\n".join(_contract_lines(events, contracts, pid)) + "\n"
    for ledger_e in seat["cash_flow"]:
        if ledger_e.get("line"):
            scene_links.append(SceneLink(label=f"{ledger_e['label']}（{ledger_e.get('round_num')}）", line=ledger_e["line"]))

    header_text = f"# 執筆素材: あなた（{pid}）の試合記録\n"
    footer_text = (
        "---\n"
        "上記の素材だけを使い、あなた自身の一人称視点で試合を振り返るブログ記事をMarkdownで"
        "書いてください。1行目は「# タイトル」。数字は素材の記載のみを使うこと。\n"
    )
    fixed_text = header_text + sec1 + sec2 + sec3 + sec4 + sec5
    fixed_chars = len(fixed_text) + len(footer_text)

    post_game_text, post_game_submitted = _post_game_reflection_text(events, pid)
    if post_game_submitted:
        sec9 = f"## 9. あなたの試合直後の総括（試合直後にあなた自身が書いたもの・原文）\n{post_game_text}\n"
    else:
        sec9 = "## 9. あなたの試合直後の総括（試合直後にあなた自身が書いたもの・原文）\n（提出されなかった）\n"

    window = _vote_slots(events)
    strategy_lanes = _strategy_lanes(index_entries, pid)
    own_messages = _gather_messages(events, pid)
    message_lanes = _message_lanes(own_messages, pid)
    memory_lanes = _memory_lanes(seat["round_memos"])

    sec6_header = "## 6. あなたの内心メモ（原文、巡ごと）"
    sec7_header = "## 7. あなたの発言とDM（原文、巡ごと）"
    sec8_header = "## 8. あなたの投票ごとの引き継ぎメモ（原文）"

    # レンダリング時の見出し・省略注記の分だけ先に予約し、アイテム本文の予算を
    # 「最終出力がmax_total_charsを超えない」範囲に確実に収める（安全側の見積り）。
    strategy_overhead = _section_render_overhead(sec6_header, strategy_lanes, window)
    message_overhead = _section_render_overhead(sec7_header, message_lanes, window)
    memory_overhead = _section_render_overhead(sec8_header, memory_lanes, window)

    variable_budget_total = (
        max_total_chars - len(system_prompt) - fixed_chars - len(sec9)
        - strategy_overhead - message_overhead - memory_overhead
    )
    if variable_budget_total < 0:
        variable_budget_total = 0

    # 3分割（6章・7章・8章）。使い残しは次の章へ繰り越す（6→7→8の順）
    third = variable_budget_total // 3
    strategy_result = _fill_budget(strategy_lanes, window, third)
    leftover1 = third - strategy_result.used_chars
    message_budget = third + max(0, leftover1)
    message_result = _fill_budget(message_lanes, window, message_budget)
    memory_budget = variable_budget_total - strategy_result.used_chars - message_result.used_chars
    memory_result = _fill_budget(memory_lanes, window, max(0, memory_budget))

    sec6 = _render_filled_section(sec6_header, strategy_result, window)
    sec7 = _render_filled_section(sec7_header, message_result, window)
    sec8 = _render_filled_section(sec8_header, memory_result, window)

    for m in own_messages:
        # scene_links（場面への直リンク）は「本人が発した/受け取った」ものだけに絞る
        # （own_messagesには公開情報として他席の全体発言も含むが、他席の発言へ
        # リンクを貼っても「この手記の場面」としては弱いため対象外）。
        if m.get("line") and (m["from"] == pid or m.get("to") == pid):
            kind_label = {"broadcast": "あなたの全体発言", "dm": "DM"}[m["kind"]]
            scene_links.append(SceneLink(label=f"{kind_label}（R{m['round_num']}V{m['vote_num']}）", line=m["line"]))

    user_prompt = (
        header_text + sec1 + sec2 + sec3 + sec4 + sec5 + sec6 + sec7 + sec8 + sec9 + footer_text
    )

    stats = {
        "pid": pid,
        "model_label": model_label,
        "system_chars": len(system_prompt),
        "user_chars": len(user_prompt),
        "total_chars": len(system_prompt) + len(user_prompt),
        "strategy_selected": sum(len(v) for v in strategy_result.selected_by_slot.values()),
        "strategy_omitted": sum(strategy_result.omitted_by_slot.values()),
        "message_selected": sum(len(v) for v in message_result.selected_by_slot.values()),
        "message_omitted": sum(message_result.omitted_by_slot.values()),
        "memory_selected": sum(len(v) for v in memory_result.selected_by_slot.values()),
        "memory_omitted": sum(memory_result.omitted_by_slot.values()),
        "post_game_reflection_submitted": post_game_submitted,
    }

    # 引用の照合用の原文プール: 実際に素材へ採用された（＝予算で落とされていない）行のみ。
    # 1行=1照合単位（labelプレフィックスがあっても部分一致で引用文字列を検出できる）。
    quote_sources = [line for line in user_prompt.splitlines() if line.strip()]

    return SelfMemoirMaterial(
        pid=pid, model_label=model_label, system_prompt=system_prompt, user_prompt=user_prompt,
        stats=stats, scene_links=scene_links, quote_sources=quote_sources,
    )
