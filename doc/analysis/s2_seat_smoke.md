# 第2戦18席 疎通確認・文面長さ見積り（サイクル7.0 Step 3・7.0a）

2026-10-23実施。各席へ`tools/prompt_smoke.py`で実際のR1V1交渉プロンプトを1回ずつ送り、
応答時間・費用・`finish_reason`・解析結果を確認した（1モデル1回、フォアグラウンドで
1本ずつ実行。CLAUDE.md過去の落とし穴⑮・⑯対策）。文面長さの見積りはAPIを呼ばず、
ローカルでBotを使った通しで計測した（§後半）。

サイクル7.0aで、Step 3で失敗していたOpenAI 3席（S2_OA_H/M/L）をdevrelay側の対応後に
再確認した。あわせてGrok 4.7（S2_XA_H）を同じ条件でもう1回呼び、所要時間・
reasoning_tokensの揺れを確認した（いずれもコード変更なし）。

## 疎通確認結果（18席）

| 席キー | モデル | 結果 | 所要時間 | 実測費用 | reasoning_tokens | finish_reason |
| --- | --- | --- | --- | --- | --- | --- |
| S2_AN_H1 | claude-fable-5-1 | OK | 18.9秒 | $0.1330 | 714 | end_turn |
| S2_AN_H2 | claude-opus-5-5 | OK | 5.1秒 | $0.0386 | 0 | end_turn |
| S2_AN_M | claude-sonnet-5-5 | OK | 4.3秒 | $0.0193 | 0 | end_turn |
| S2_AN_L | claude-haiku-5-5 | OK | 2.9秒 | $0.0010 | 0 | end_turn |
| S2_OA_H | devrelay/gpt-6-astra | OK（サイクル7.0aで解消、注3） | 28.2秒 | $0（subscription） | 0 | success |
| S2_OA_M | devrelay/gpt-6-sol | OK（サイクル7.0aで解消、注3） | 16.2秒 | $0（subscription） | 0 | success |
| S2_OA_L | devrelay/gpt-6-luna | OK（サイクル7.0aで解消、注3） | 5.3秒 | $0（subscription） | 0 | success |
| S2_GO_H | gemini-4-argon | OK | 5.5秒 | $0.0112 | 0 | stop |
| S2_GO_M | gemini-3.8-flash | OK | 6.4秒 | $0.0049 | 0 | stop |
| S2_GO_L | gemini-3.1-flash-lite | OK | 1.4秒 | $0.0016 | 0 | stop |
| S2_XA_H | grok-4.7 | OK（注1。サイクル7.0bで`reasoning_effort="low"`採用） | 14.2〜32.2秒（low、3回） | $0.0075〜0.0169 | 527〜1,523 | stop |
| S2_XA_M | grok-4.3 | OK | 5.1秒 | $0.0072 | 306 | stop |
| S2_XA_L | grok-4.20-0309-non-reasoning | OK | 1.8秒 | $0.0064 | 0 | stop |
| S2_MO_H | kimi-k3 | OK | 8.6秒 | $0.0432 | 0 | stop |
| S2_MO_M | kimi-k2.7-code | OK（注2） | 29.3秒 | $0.0075 | 1,363 | stop |
| S2_MO_L | kimi-k2.6 | OK | 6.9秒 | $0.0068 | 1 | stop |
| S2_DS_H | deepseek-v4-pro | OK | 3.3秒 | $0.0038 | 0 | stop |
| S2_DS_L | deepseek-flash | OK | 3.4秒 | $0.0009 | 0 | stop |

**18/18席が疎通確認OK（サイクル7.0aでOpenAI3席も解消）。**

### OpenAI 3席の失敗（Step 3時点）とサイクル7.0aでの解消（注3）

`codex exec -m gpt-6-astra`等では**Codex CLI自体は応答する**ことを事前に確認したが
（Step 0・本サイクルでCodex CLIを0.147.0→0.163.0に更新済み、models_cache.jsonに
gpt-6-astra/gpt-6-sol/gpt-6-lunaが掲載されている）、**DevRelayサーバー側のraw-completion
エンドポイント（`ai="codex"`経路）は、この3モデル名をまだ認識しない**
（`aiUnavailable: Unknown Codex model: "<name>"`）。既存の`DR_SOL`（model_id=
`devrelay/gpt-5.6-sol`）で同じ疎通確認をしたところ正常に応答した（13.4秒、$0・
finish_reason="success"）ため、**DevRelay自体の不調ではなく、DevRelayサーバー側の
Codexモデル許可リストがGPT-6世代をまだ知らないこと**が原因と判明した。

Codex CLIのモデル一覧（クライアント側、`~/.codex/models_cache.json`）と、DevRelayサーバー
側が受け付けるモデル名の一覧は**別物**であることが今回判明した新しい知見である（本件を
通して分かった恒久的な知見として残す）。

決定1の「Astraが一覧に無い・呼べないなら登録せず報告して止まる」に該当したため、
サイクル7.0では**S2_OA_H/M/Lの3席は`llm/models.py`に登録済みだが、実際には呼べない**
状態のまま止まっていた（本プロジェクトからdevrelayプロジェクトには手を入れていない）。

### 注3: サイクル7.0aでの再確認（解消済み）

けいすけさんがdevrelay側にgpt-6-astra/gpt-6-sol/gpt-6-lunaの3件を追加し
（commit f13c693）、`pm2 restart devrelay-server`を実施。サイクル7.0aで3席とも
`tools/prompt_smoke.py`で再確認したところ、3席とも正常に応答した
（`finish_reason='success'`、`parse_response`も解析成功、実測費用は3席とも
$0＝subscription課金のまま）。**本プロジェクト側は`llm/models.py`のS2_OA_*エントリを
一切変更していない**（登録時点のまま正しかった）。これで`--roster L18_S2`を
そのまま使って試走に進める。

### 注1: Grok 4.7（S2_XA_H）の所要時間が長い → サイクル7.0bで`reasoning_effort="low"`を採用

1回目（Step 3）はトリビアルな確認呼び出し1回で**124.9秒**（既定タイムアウト180秒の
約70%）、reasoning_tokens=7,010（登録時の暫定予約9,728に対し72%）だった。サイクル7.0aで
同条件のまま2回目を実測したところ、**161.9秒**（タイムアウト180秒の約90%）・
reasoning_tokens=9,375（予約9,728に対し96%）と、1回目よりさらに時間・トークンの両方が
伸びた。2回の実測だけで「常にこの水準」とは言えないが、**トリビアルな1コールでも
ばらつきが大きく、タイムアウト180秒・予約9,728トークンのどちらにも十分な余裕が
無いことが分かった**（H4（grok-4.6）が辿った「60秒の壁」と同種の懸念）。

xAI公式ドキュメント（docs.x.ai /model-capabilities/text/reasoning、2026-10-23確認）に
「grok-4.7, grok-4.6, and grok-4.5 support the `reasoning_effort` parameter」
（low/medium/high=既定/xhigh。思考の完全停止は不可）とあったため、サイクル7.0bで
`reasoning_effort="low"`を3回実測した（`llm/models.py`はまだ変更せず、`/tmp`の
使い捨てスクリプトで`MODEL_REGISTRY`のコピーを差し替えて実行。API 3コール・計$0.0377）。

| 回 | reasoning_effort | 所要時間 | reasoning | output | finish_reason | 実測費用 | parse_response |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 7.0 Step3 | 未指定（＝high） | 124.9秒 | 7,010 | — | stop | $0.0539 | OK |
| 7.0a | 未指定（＝high） | 161.9秒 | 9,375 | — | stop | — | OK |
| 7.0b-1 | low | 20.7秒 | 855 | 210 | stop | $0.01688 | OK（broadcast） |
| 7.0b-2 | low | **32.2秒** | **1,523** | 162 | stop | $0.01331 | OK（broadcast） |
| 7.0b-3 | low | 14.2秒 | 527 | 191 | stop | $0.00750 | OK（dm） |

low の3回で**所要時間は161.9秒→最大32.2秒（約5分の1）**、**reasoningは9,375→最大1,523
（約6分の1）**まで下がった。3回とも`finish_reason='stop'`・JSON解析成功（broadcast 2件・
dm 1件、戦略の筋も通っていた）。けいすけさんがこの実測を見て**「low で grok-4.7 を使う」
と決定・承認**し、サイクル7.0bで`llm/models.py`の`S2_XA_H`に反映した
（`extra_params={"reasoning_effort": "low"}`、`hidden_thinking_reserve_tokens`は
9728→2048（ceil(1523×1.2)=1828を512単位へ切り上げ）、`timeout_seconds=180`は据え置き）。

**応答の質の変化は3回では判断できない。** 3回とも解析は成功し内容も筋が通っていたが、
high との受け答えの優劣を3回の比較だけで結論づけることはできない。18席の企画として
ハイ席に置く意味（じっくり考える席）が薄れているかどうかは、試走の実際の文面で
継続して見ること。

### 注2: Kimi K2.7 Code（S2_MO_M）の思考無効化不可とmax_tokens

`tools/prompt_smoke.py`の既定`max_tokens=1500`では、reasoning_tokensだけで1,500を
使い切り`finish_reason='length'`・JSON解析失敗（本文が思考過程の英文のみ）になった
（CLAUDE.md過去の落とし穴⑯そのもの）。**本番の実際のmax_tokens（`llm/constants.py::
DEFAULT_MAX_TOKENS=4000`）で同じ実際のR1V1交渉プロンプトを再実行したところ、
reasoning 1,363トークン＋本文137トークンで余裕を持って完走した**（`finish_reason='stop'`、
解析成功）。つまり**本番の呼び出し経路（`llm_agent.py`、max_tokens=4000）では現時点で
問題は起きていない**が、reasoning_tokensが呼び出しごとに1,363〜2,542トークンと変動が大きく
（別の簡易プロンプトでの試行では2,542トークンを観測）、より複雑な後半ラウンドの交渉
プロンプトでは4,000トークンに接近する可能性がある。けいすけさん決定4で「思考OFF不可のまま
採用」と決めた経緯から、**Step 4の試走ではこの席のfinish_reason='length'発生率を
特に注視すること**を申し送る。

## 文面の長さの見積り（18人、API呼び出しなし）

ローカルのBot（本番コードではなく、この見積り専用の一時スクリプト）を使い、実際の
`llm/prompt_builder.py::build_negotiation_prompt`でR1〜R4通しの交渉プロンプトを組み立て、
`TurnPrompt.omitted_messages`（§9.4・#64、上限超えで古い発言から省いた件数）を12人・
18人で比較した。**実際のAIを1件も呼んでいない**（Botの発言頻度・長さは見積り用に
人為的に設定した値であり、実測ではない。CLAUDE.md過去の落とし穴⑬・⑭の裏返し: 実際の
AIでしか実測できない以上、ここでは複数の仮定で挟んで方向性だけを見る）。

### 設定1: 全員が毎手番、上限近い490字のbroadcastを送る（上振れの目安）

| 人数 | P01の交渉手番合計 | 文面が省略された手番の割合 | 最大省略件数（最終ラウンド最後の投票・最終巡） |
| --- | --- | --- | --- |
| 12人 | 58 | 58.6% | 84件/116件省略 |
| 18人 | 97 | 71.1% | 142件/170件省略 |

### 設定2: 毎手番50%の確率で100〜350字のbroadcastを送る（中間的な目安）

| 人数 | P01の交渉手番合計 | 文面の平均長さ | 省略が発生した手番の割合 |
| --- | --- | --- | --- |
| 12人 | 67 | 8,000字 | 3.0%（2/67件） |
| 18人 | 64 | 10,558字 | **28.1%（18/64件）** |

### 所見

- どちらの設定でも、文面自体が15,000字を超えて送信不能になることはない（§9.4の
  「省いても上限を超える場合はそのまま返す」の仕組みにより、実際の送信文面は
  常に上限以下か、省略してもなお上限超えならそのまま送る設計）。
- **18人は12人より、上限に当たって古い発言が省かれる頻度が明確に高い**
  （中間的な設定で約9倍: 3.0%→28.1%）。これは参加者が増えることで、同じ投票内に
  積み上がる発言数自体が増えるため構造的に避けられない。
- 実際のAI同士の交渉がどの程度の頻度・長さで発言するかは、12人の本番試走
  （`logs/llm_v0_4/`）でも巡数・会話量に依存してばらつきが大きく、今回の見積りも
  あくまで方向性（18人のほうが省略されやすい）を示すものに留まる。
- Step 4の試走実行時は、`omitted_messages`が0でない手番の件数・最大件数を記録に残し、
  実測で裏取りすることを推奨する（既存ログに`TurnPrompt.omitted_messages`相当の値が
  記録されているかは別途確認が必要。見つからない場合は記録を追加する申し送り）。

## けいすけさんに決めていただきたいこと

1. **OpenAI 3席（S2_OA_H/M/L）の扱い**: ~~DevRelayサーバー側がGPT-6世代のCodexモデル名を
   まだ認識しないため、現時点では呼べない。~~ **サイクル7.0aで解消済み（注3）。**
   devrelay側に3件追加・`pm2 restart devrelay-server`後、3席とも疎通確認OKになった。
   `llm/models.py`のS2_OA_*エントリは変更していない（登録自体が正しかった）。
   `--roster L18_S2`をそのまま使って試走に進められる。フォールバック案
   （`doc/analysis/s2_trial_prep.md`のGPT-5.6世代への差し替え）は不要になったが、
   控えとして残す。
2. **Grok 4.7（S2_XA_H）の所要時間**: ~~1回目124.9秒・2回目161.9秒（サイクル7.0a実測）と、
   トリビアルな1コールでも揺れが大きく、2回目はタイムアウト180秒の約90%・予約
   トークン9,728の約96%まで使っている。~~ **けいすけさん決定（サイクル7.0b）: low で
   grok-4.7 を使う。** Grok 4.6への差し替えはしない。`reasoning_effort="low"`を3回
   実測したところ14.2〜32.2秒・reasoning 527〜1,523まで下がった（注1参照）。
   `llm/models.py`の`S2_XA_H`に反映済み（`hidden_thinking_reserve_tokens`は2048へ
   見直し、`timeout_seconds=180`は据え置き）。応答の質の変化は3回では判断できないため、
   試走の実際の文面で継続して見ること。
