# 第2戦18席 試走の準備（サイクル7.0 Step 4）

本サイクルでは試走・本番は実行しない（コマンドの用意と費用上限の案の提示のみ）。

## ビューアの18席表示確認

`scripts/dry_run.py --seed 42 --contracts --num-players 18`（本サイクルで追加した
`--num-players`フラグ。AIは呼ばない・費用0円）で18人のJSONLログを作り、S2の18席の
`model_id`で手作りした`seat_map.json`を添えて、ビューアAPI（`uv run python -m
viewer.server`）を実際に起動して確認した。

- `/api/games` の一覧に`"num_players": 18`で正しく載る
- `/api/games/{id}/overview` の`roster`配列が18件とも正しく返る（`claude-fable-5-1`
  等、S2の`model_id`がラベルとして正しく解決される）
- `/api/games/{id}/rounds/{n}` も18人分のremaining_ids/eliminated_ids含め正常に返る
  （R1V3で18人→7人、V4で7人→3人、といった推移も正しく記録・表示される）
- `/api/games/{id}/money`・`/api/games/{id}/highlights`・`/api/games/{id}/seats/{pid}`
  も全てHTTP 200で正常
- 静的CSS（`viewer/static/style.css`）を確認したところ、席の一覧は`<table
  class="roster-table">`（`width:100%`、行で積む）であり、12列・12行を前提にした
  固定グリッドではないため、18席でも横に崩れる構造上の懸念は無い（コード上の
  真の12固定グリッドは`blog/cards.py`の総括カードのみで、ビューアには存在しない）

**制約**: 本環境にはヘッドレスブラウザ（playwright等）が無く、実際のスクリーンショット
による目視確認はできなかった。上記はAPIの戻り値とCSSの静的確認による構造上の裏付けで
あり、実際のブラウザ描画の最終確認はけいすけさんが`uv run python -m viewer.server`を
起動し`/watch?game=dry_run_seed42_18p`を一度開いて確認することを推奨する。

## 試走を起動するコマンド（実行はしない）

### 本来の構成（L18_S2、全18席）

```bash
uv run python scripts/llm_trial.py --roster L18_S2 --seed 7001 \
    --stop-after-round 1 --parallel 4 \
    --per-seat-cap-usd 1.2 --game-cap-usd 4.0 \
    --game-id t18_r1_7001
```

**注意**: `S2_OA_H/M/L`（OpenAI、DevRelay/Codex経路）の3席は、`doc/analysis/
s2_seat_smoke.md`に記録したとおり現時点でDevRelayサーバー側がGPT-6世代のCodexモデル名を
認識せず失敗する（`aiUnavailable: Unknown Codex model`）。このまま実行すると、
`llm_trial.py`の事前疎通確認（`--preflight-only`または本走り冒頭のpreflight probe）で
この3席が失敗として記録される見込み。DevRelay側の対応を待つか、下記フォールバックを使うこと。

### フォールバック（OpenAI3席を既存のGPT-5.6世代DevRelay席に差し替え）

`NAMED_ROSTERS["L18_S2"]`は変更せず、`--roster`に直書きで差し替える案:

```bash
uv run python scripts/llm_trial.py \
    --roster "S2_AN_H1,S2_AN_H2,S2_AN_M,S2_AN_L,DR_TERRA,DR_SOL,DR_LUNA,S2_GO_H,S2_GO_M,S2_GO_L,S2_XA_H,S2_XA_M,S2_XA_L,S2_MO_H,S2_MO_M,S2_MO_L,S2_DS_H,S2_DS_L" \
    --seed 7001 --stop-after-round 1 --parallel 4 \
    --per-seat-cap-usd 1.2 --game-cap-usd 4.0 \
    --game-id t18_r1_7001_fallback
```

（`DR_TERRA`=gpt-5.6-terra・`DR_SOL`=gpt-5.6-sol・`DR_LUNA`=gpt-5.6-luna。いずれも
本サイクルのStep 3疎通確認で`DR_SOL`を実測成功済み。GPT-6世代ではなくGPT-5.6世代に
なる点で指示書の顔ぶれとは異なるため、あくまで繋ぎのフォールバック）

## 費用上限（案）

Step 3実測（`doc/analysis/s2_seat_smoke.md`、1コールずつの実測費用）から:

| 項目 | 値 | 根拠 |
| --- | --- | --- |
| 実測した1コールの最大費用 | $0.1330（S2_AN_H1、claude-fable-5-1。初回のキャッシュ書き込みを含む） | Step 3実測 |
| 18席の1コールずつの費用合計 | $0.339（OpenAI3席は$0=subscription想定または失敗） | Step 3実測の単純合計 |
| 既存12席・R1だけの実績値（`scripts/llm_trial.py`docstringの実例） | per-seat 0.5〜2.0ドル・game 3.0〜6.0ドル | 既存コードのコメント |

**提案**:
- `--per-seat-cap-usd 1.2`（最大実測0.133ドル × 安全率1.5 × R1V1想定コール数6回）。
  Fable 5.1・Grok 4.7のような高額・長時間の席1つが暴走しても、1.2ドルで止まる
- `--game-cap-usd 4.0`（18席の1コールずつの合計0.339ドル × 想定コール数6回 × 安全率1.2
  ≈ 2.44ドルを切り上げ、既存12席実績の6.0ドルより低いが18席分を見込んで余裕を持たせた）

いずれも**実測1回ぶんの数字からの見積りであり、実際のR1（複数投票・最大10巡の交渉）を
通した実測ではない**（CLAUDE.md過去の落とし穴⑬: 巡数・会話量が実際の試合に近い通しでしか
実測できない）。Step 4はあくまで試走の準備であり、**実際の数字はけいすけさんが試走を
1本動かした後に見直すこと**を前提とする。

## 特に注視してほしい2点（試走実行時）

1. **Grok 4.7（S2_XA_H）**: トリビアルな確認で124.9秒（タイムアウト180秒の約70%）。
   実際の交渉プロンプトではより時間がかかる可能性がある。
2. **Kimi K2.7 Code（S2_MO_M）**: 思考を無効化できないモデル。本番のmax_tokens=4000では
   今回の確認では問題なかったが、reasoning_tokensの変動が大きい（1,363〜2,542トークンを
   観測）。`finish_reason='length'`の発生有無を特に見ること。

## 未対応（本サイクルの対象範囲外、申し送り）

- `blog/cards.py`の総括カード（`grid-template-columns: repeat(6,1fr)`、12枚固定）は
  18枚だと崩れる。第2戦のブログ公開を行うサイクルで対応する。
- `sim/scenarios.py`のV2〜V11（組・居座り・掛け持ち等）は18人化していない
  （本サイクルはV1の18人版=`V1_18p`のみ追加。型B/型Cを使う18人検証が必要になれば
  別サイクルで対応）。
