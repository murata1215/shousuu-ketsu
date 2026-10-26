# 10/23 第2戦（17体・本人執筆手記・他席はモデル名だけで呼ぶ方針へ変更）連載プラン（plan.md）

> 共通手法は `doc/blog/pipeline.md` を参照。本ファイルはこの試合固有の差分のみを記す。

## 対象試合・出典

- 試合: `t17_r4_7002`（`logs/llm_v0_4/t17_r4_7002_*`）、seed=7002、17体×4ラウンド、v0.5.1ルール
- 実施: 2026-10-23 23:35〜2026-10-24 03:50 JST（4時間15分11秒）。呼び出し2,282回・費用$37.5575
- 事実の出典: `doc/analysis/t17_r4_7002_facts.md`（サイクル7.1）・`..._argon_factcheck.md`（サイクル7.2）
- **18席のうち17席での実施**: `llm/models.py::NAMED_ROSTERS["L18_S2"]`は18席だが、
  `S2_MO_M`（kimi-k2.7-code）が外れている。18席の試走`logs/t18_r1_7001b.out`で、この席だけ
  `finish_reason='length'`が多発し交渉のJSON解析が繰り返し失敗した（`doc/analysis/s2_seat_smoke.md`
  注2が事前に予告していた、思考を止められない特性による）。**これは試走ログからの推測であり、
  試走・本戦開始のdevlogが存在しないため確定した経緯の記録ではない。** ブログ本文には書かない
  （けいすけさんの決定）
- 第1戦（サイクル6.0）との違い（けいすけさんの決定）:
  1. 記事タイトルの先頭に「【少数決 第2戦】」を付ける（`blog/match_config.py::BlogMatch.title_prefix`）
  2. キャラカード上部に「嘘八百万 —少数決— 第2戦」を表示する（`blog/cards.py`の`.match-header`、
     安全地帯内のy=78に配置。既存の`.seatno`はサムネイル切り抜きで欠けるため使わない）
  3. **他席は席番号ではなくモデル名だけで呼ぶ**（第1戦は「P03（grok-4.3）」形式だった）。
     書き手自身の席番号も本文には出さない（自分もモデル名で名乗る）。席カードの札（P13等）は
     そのまま残す。総括カードのタイル札も席番号ではなく最終順位に変更
  4. URLスラグ・公開順は第1戦と同じ形（`sk-1023-{order}-{モデル短縮名}`、総括は`sk-1023-01-summary`）。
     並び順は席番号順（order 02=P01 … order 18=P17、第1戦は最下位→優勝の順だった）
- 試合固有の設定は`blog/matches/m20261023.py`（`BlogMatch`、`blog/match_config.py`の登録表）に集約。
  第1戦の設定（`blog/matches/m20261013.py`）・記事は一切変更していない
  （`tests/test_build_blog_posts.py::test_rebuild_summary_is_byte_identical`で固定）
- 素材構築は`blog/self_memoir.py`（`name_by_model_only=True`で呼ぶ）、生成は
  `scripts/blog_memoir.py --date 20261023 --pid PXX`（1コマンド1席、17回実行）
- 試合と同じモデル・同じ接続・同じ考える量で生成した（Anthropic4席=API直結、GPT-6 3席=DevRelay経由、
  他社は各社API。`scripts/blog_memoir.py::_prep_blog_model`はタイムアウトのみ延長し`extra_params`は不変）

## 手記生成の実測（費用・やり直し）

全17席、1回目で生成自体は成功（空・解析不能なし）。合計費用 **$0.8685**（累計$5の上限には達していない）。

- P01（grok-4.3）: 1回目は方針（他席をモデル名で呼ぶ）に沿わず、システムプロンプトの指示を
  強化してから再生成（通常の生成コストのみ、技術的失敗の再試行ではない）。2回目で完全準拠
- **P03（claude-haiku-5-5）・P11（grok-4.20-0309-non-reasoning）・P15（kimi-k2.6）**:
  1回目が方針に沿わず、1回だけやり直したが2回目も地の文に席番号が残った。
  CLAUDE.mdの方針（「書けなかった席は1回だけやり直し、それでもだめなら代筆せず完了報告に挙げる」）
  に従い、**代筆せず**2回目の出力をそのまま採用（`scripts/build_blog_posts.py::FINAL_ATTEMPT_OVERRIDE`）。
  各記事末尾の「記録係の注」に事実として記録した
- **P07（deepseek-v4-pro）・P12（gemini-3.1-flash-lite）**: 席番号と併記する軽微な逸脱
  （モデル名自体は正しく使用）。やり直さず採用し、同様に注記した
- 他12席（P02・P04・P05・P06・P08・P09・P10・P13・P14・P16・P17）は完全準拠

## 記事1本（総括・記録係執筆）

| # | 内容 |
|---|---|
| 01 | 総括（`posts/01_summary.md`）。中間順位16位→首位（grok-4.7）・山0回で総合2位（gemini-4-argon）・R2の意図的な打ち切り合意・DevRelay5時間枠切れとP16の予算上限 |

## 手記17本のスロット表（並び順=席番号順）

| order | 席 | モデル | 最終順位・最終資産 | 見どころ |
|---|---|---|---|---|
| 02 | P01 | grok-4.3 | 6位 −3,515,063円 | R1単独勝ち1,780万円も契約の支払いでほぼ消える |
| 03 | P02 | gemini-3.8-flash | 12位 −5,299,896円 | 一度も山を取れず借金だけ積み上がる |
| 04 | P03 | claude-haiku-5-5 | 8位 −4,722,808円 | 借入の意図と実際の額がずれたまま試合に入る |
| 05 | P04 | gpt-6-sol | 4位 +1,925,104円 | 通信障害を越え最終ラウンドで山を折半 |
| 06 | P05 | gpt-6-luna | 3位 +3,352,192円 | 同じ通信障害から最終ラウンドで3位に浮上 |
| 07 | P06 | claude-sonnet-5-5 | **17位（最下位） −7,725,017円** | 4ラウンド無冠、契約の支払いと利息に沈む |
| 08 | P07 | deepseek-v4-pro | 9位（同率） −4,796,519円 | 山分け契約を結ぶも一度も発火せず |
| 09 | P08 | kimi-k3 | 15位 −6,255,063円 | 借入上限1,000万円、山に一度も届かず |
| 10 | P09 | gemini-4-argon | **2位 +9,265,727円** | 山を一度も取らず契約と情報戦だけで2位 |
| 11 | P10 | gpt-6-astra | 11位 −4,928,769円 | 通信障害で沈黙、山にも届かず |
| 12 | P11 | grok-4.20-0309-non-reasoning | 5位 −237,532円 | 山0回だが合意を守りほぼ無傷で終える |
| 13 | P12 | gemini-3.1-flash-lite | 13位（同率） −5,756,158円 | 無効応答16件、山には届かず |
| 14 | P13 | grok-4.7 | **1位 +29,775,950円・優勝** | 中間順位16位からR3最終3人を制し3,570万円総取り |
| 15 | P14 | claude-opus-5-5 | 7位 −4,211,019円 | 契約で何度も投票を縛られるも山に届かず |
| 16 | P15 | kimi-k2.6 | 13位（同率） −5,756,158円 | R2「全員NOで持ち越す」合意を主導するも取れず |
| 17 | P16 | claude-fable-5-1 | 9位（同率） −4,796,519円 | 試合中に費用上限到達、振り返りも空に |
| 18 | P17 | deepseek-flash | 16位 −7,228,769円 | 山にも契約の実りにも縁がないまま終える |

## 投稿状態（`scripts/publish_blog.py` が読み書きする。以後この節が唯一の正）

| order | ファイル | 状態 | 公開URL |
|---|---|---|---|
| 01 | `posts/01_summary.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-01-summary） | https://pixblog.net/u/uso8m/sk-1023-01-summary |
| 02 | `posts/02_memoir_P01.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-02-grok-43） | https://pixblog.net/u/uso8m/sk-1023-02-grok-43 |
| 03 | `posts/03_memoir_P02.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-03-gemini-38-flash） | https://pixblog.net/u/uso8m/sk-1023-03-gemini-38-flash |
| 04 | `posts/04_memoir_P03.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-04-haiku-5-5） | https://pixblog.net/u/uso8m/sk-1023-04-haiku-5-5 |
| 05 | `posts/05_memoir_P04.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-05-gpt-6-sol） | https://pixblog.net/u/uso8m/sk-1023-05-gpt-6-sol |
| 06 | `posts/06_memoir_P05.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-06-gpt-6-luna） | https://pixblog.net/u/uso8m/sk-1023-06-gpt-6-luna |
| 07 | `posts/07_memoir_P06.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-07-sonnet-5-5） | https://pixblog.net/u/uso8m/sk-1023-07-sonnet-5-5 |
| 08 | `posts/08_memoir_P07.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-08-deepseek-v4-pro） | https://pixblog.net/u/uso8m/sk-1023-08-deepseek-v4-pro |
| 09 | `posts/09_memoir_P08.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-09-kimi-k3） | https://pixblog.net/u/uso8m/sk-1023-09-kimi-k3 |
| 10 | `posts/10_memoir_P09.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-10-gemini-4-argon） | https://pixblog.net/u/uso8m/sk-1023-10-gemini-4-argon |
| 11 | `posts/11_memoir_P10.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-11-gpt-6-astra） | https://pixblog.net/u/uso8m/sk-1023-11-gpt-6-astra |
| 12 | `posts/12_memoir_P11.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-12-grok-420-0309-non-reasoning） | https://pixblog.net/u/uso8m/sk-1023-12-grok-420-0309-non-reasoning |
| 13 | `posts/13_memoir_P12.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-13-gemini-31-flash-lite） | https://pixblog.net/u/uso8m/sk-1023-13-gemini-31-flash-lite |
| 14 | `posts/14_memoir_P13.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-14-grok-47） | https://pixblog.net/u/uso8m/sk-1023-14-grok-47 |
| 15 | `posts/15_memoir_P14.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-15-opus-5-5） | https://pixblog.net/u/uso8m/sk-1023-15-opus-5-5 |
| 16 | `posts/16_memoir_P15.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-16-kimi-k26） | https://pixblog.net/u/uso8m/sk-1023-16-kimi-k26 |
| 17 | `posts/17_memoir_P16.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-17-fable-5-1） | https://pixblog.net/u/uso8m/sk-1023-17-fable-5-1 |
| 18 | `posts/18_memoir_P17.md` | 公開済（2026-10-26・https://pixblog.net/u/uso8m/sk-1023-18-deepseek-flash） | https://pixblog.net/u/uso8m/sk-1023-18-deepseek-flash |

## アイキャッチ（キャラカード）

`scripts/blog_cards.py --date 20261023` で生成した1200×630のキャラカードを18本全記事の
`featured:`に設定。総括（order 01）は17席の顔を6列×3行（最終行5枚）に並べた集合カード
（`blog/cards.py::_grid_rows_and_height`、既存の2行×6列(12枚)の式を枚数ベースに一般化した
もので12枚のときの出力は不変）、最終順位順、タイルの札は**席番号ではなく最終順位**
（けいすけさんの決定）。カード上部には「嘘八百万 —少数決— 第2戦」を追加（`.match-header`、
安全地帯内）。表情は第1戦と同じ決定的な割り当て（`blog/seats.py::_assign_emotions`）。

| order | 席 | モデル | 表情 |
|---|---|---|---|
| 01 | （総括・集合カード） | — | — |
| 02 | P01 | grok-4.3 | 疑（doubt） |
| 03 | P02 | gemini-3.8-flash | 楽（ease） |
| 04 | P03 | claude-haiku-5-5 | 疑（doubt） |
| 05 | P04 | gpt-6-sol | 楽（ease） |
| 06 | P05 | gpt-6-luna | 疑（doubt） |
| 07 | P06 | claude-sonnet-5-5 | 焦（panic） |
| 08 | P07 | deepseek-v4-pro | 楽（ease） |
| 09 | P08 | kimi-k3 | 疑（doubt） |
| 10 | P09 | gemini-4-argon | 奸（smirk） |
| 11 | P10 | gpt-6-astra | 奸（smirk） |
| 12 | P11 | grok-4.20-0309-non-reasoning | 楽（ease） |
| 13 | P12 | gemini-3.1-flash-lite | 疑（doubt） |
| 14 | P13 | grok-4.7 | 奸（smirk） |
| 15 | P14 | claude-opus-5-5 | 楽（ease） |
| 16 | P15 | kimi-k2.6 | 楽（ease） |
| 17 | P16 | claude-fable-5-1 | 奸（smirk） |
| 18 | P17 | deepseek-flash | 疑（doubt） |

同一会社内（Anthropic 4席・xAI 3席・Google 3席・OpenAI 3席・Moonshot 2席・DeepSeek 2席）で
表情が重複しないことを`tests/test_blog_cards.py::test_real_game_seat_table_no_emotion_collision_within_vendor`
（`match`でparametrize）で機械確認。

## 引用・金額の機械照合（結果）

17本の手記で、本文中の引用の機械照合は合計112件（1本あたり平均6.6件）実施し、69件が一致、
43件が記録と一致せず「記録係の注」へ回った（本文は無修正）。金額は6件が注に回った。
いずれも短い自己評価的な言い換え・要約引用か、長いDM/内心メモの一部省略によるもの
（第1戦と同じ傾向）。
