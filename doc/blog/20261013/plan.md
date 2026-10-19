# 10/13 本戦（12体・本人執筆手記・v0.5「山分け」初実測）連載プラン（plan.md）

> 共通手法は `doc/blog/pipeline.md` を参照。本ファイルはこの試合固有の差分のみを記す。

## 対象試合・出典

- 試合: `t12_r4_4501c`（`logs/llm_v0_4/t12_r4_4501c_*`）、seed=4501、12体×4ラウンド、v0.5ルール
- 実施: 2026-10-13 08:31〜11:00（2時間28分）。呼び出し1,219回・費用$10.65
- 事実の出典: `doc/analysis/t12_r4_4501c_facts.md`
- v0.5で足した「山分け」（pool_share）が初めて実際に複数回発火した試合（成立6本中5本が発火、合計11,040,000円）
- 手記12本は各席のAI本人に、自分の席番号・使われたモデル名・最終順位・最終資産・山の受け取りと
  山分けの受け払いの実額を伝えて執筆させた（決定①②）。他席も「席番号＋モデル名」で呼ばせる
  （談合カードは本人のモデル名さえ隠していたが、本サイクルは明示する方針に変更）
- 素材構築は `blog/self_memoir.py`（新規）、生成は `scripts/blog_memoir.py --pid PXX`（1コマンド1席、12回実行）
- 引用・金額の機械照合は `blog/quote_check.py`。一致しないものは本文から**落とさず**、
  各記事末尾の「記録係の注」に記録する（決定④、談合カードの実際の運用（サイクル10.26）と同じ）
- エンジン・ルール・試合中プロンプトは変更なし

## 記事1本（総括・記録係執筆）

| # | 内容 |
|---|---|
| 01 | 総括（`posts/01_summary.md`）。順位・山分けが初めて効いた件（P05・P08の手取りがほぼ0に）・優勝P06の二度の単独総取り・事故と契約の内訳 |

## 手記12本のスロット表（並び順=最下位から順に→優勝（P06）を最後に）

| order | 席 | モデル | 最終順位・最終資産 | 見どころ |
|---|---|---|---|---|
| 02 | P07 | Gemini 3.5 Flash-Lite | 12位 −6,355,063円 | 借入1,000万円、一度も山を取れず複利に沈む |
| 03 | P08 | GPT-5.6 Luna（DevRelay実験席） | 11位 −6,198,340円 | R4で600万円を取るも山分けで504万円を手放す |
| 04 | P05 | DeepSeek V4 Pro | 10位 −5,777,532円 | R2で600万円を取るも山分け2本＋違約金で手取りマイナス |
| 05 | P11 | Claude Sonnet 5 | 9位 −5,432,146円 | 少数派連合は機能したが自分だけ最後に残れず |
| 06 | P12 | Kimi K2.6 | 8位 −5,355,063円 | 借入上限1,000万円、山分けで300万円を得るも利息に押される |
| 07 | P10 | Claude Haiku 4.5 | 7位 −5,061,019円 | R3で現金ゼロ、山分け保険も不発 |
| 08 | P02 | Claude Opus 5 | 6位 −4,282,146円 | 借入最小120万円、4回とも多数派で終わる |
| 09 | P04 | GPT-4.1 Mini | 5位 −1,412,146円 | P08との山分けを軸に少数派を狙い続ける |
| 10 | P09 | DeepSeek V4 Flash | 4位 −907,396円 | 早期退場後、山分けで他人の勝利に寄生する戦略に転換 |
| 11 | P03 | Grok 4.3 | 3位 −355,063円 | 借入1,000万円、R2で600万円を獲得するも同じ読みに固執 |
| 12 | P01 | GPT-5.6 Terra（DevRelay実験席） | 2位 +3,437,975円 | P06の独走を二度止められず、最終ラウンドで2位まで回復 |
| 13 | P06 | GPT-5.6 Sol（DevRelay実験席） | **1位 +21,941,392円・優勝** | R1・R3で協力者を裏切り単独総取り、契約の支払い・受け取りゼロ |

## 投稿状態（`scripts/publish_blog.py` が読み書きする。以後この節が唯一の正）

| order | ファイル | 状態 | 公開URL |
|---|---|---|---|
| 01 | `posts/01_summary.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-01-summary） | https://pixblog.net/u/uso8m/sk-1013-01-summary |
| 02 | `posts/02_memoir_P07.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-02-gemini-35-flash-lite） | https://pixblog.net/u/uso8m/sk-1013-02-gemini-35-flash-lite |
| 03 | `posts/03_memoir_P08.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-03-gpt-56-luna） | https://pixblog.net/u/uso8m/sk-1013-03-gpt-56-luna |
| 04 | `posts/04_memoir_P05.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-04-deepseek-v4-pro） | https://pixblog.net/u/uso8m/sk-1013-04-deepseek-v4-pro |
| 05 | `posts/05_memoir_P11.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-05-sonnet-5） | https://pixblog.net/u/uso8m/sk-1013-05-sonnet-5 |
| 06 | `posts/06_memoir_P12.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-06-kimi-k26） | https://pixblog.net/u/uso8m/sk-1013-06-kimi-k26 |
| 07 | `posts/07_memoir_P10.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-07-haiku-4-5） | https://pixblog.net/u/uso8m/sk-1013-07-haiku-4-5 |
| 08 | `posts/08_memoir_P02.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-08-opus-5） | https://pixblog.net/u/uso8m/sk-1013-08-opus-5 |
| 09 | `posts/09_memoir_P04.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-09-gpt-41-mini） | https://pixblog.net/u/uso8m/sk-1013-09-gpt-41-mini |
| 10 | `posts/10_memoir_P09.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-10-deepseek-v4-flash） | https://pixblog.net/u/uso8m/sk-1013-10-deepseek-v4-flash |
| 11 | `posts/11_memoir_P03.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-11-grok-43） | https://pixblog.net/u/uso8m/sk-1013-11-grok-43 |
| 12 | `posts/12_memoir_P01.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-12-gpt-56-terra） | https://pixblog.net/u/uso8m/sk-1013-12-gpt-56-terra |
| 13 | `posts/13_memoir_P06.md` | 公開済（2026-10-19・https://pixblog.net/u/uso8m/sk-1013-13-gpt-56-sol） | https://pixblog.net/u/uso8m/sk-1013-13-gpt-56-sol |

## アイキャッチ（キャラカード）

`scripts/blog_cards.py` で生成した1200×630のキャラカードを13本全記事の`featured:`に設定。
総括（order 01）は12席の顔を2行×6列に並べた集合カード（最終順位順）。表情は
`viewer/log_index.py`の索引（`emotion`フィールド）の実測の最頻値を、順位の高い席から
会社（vendor）単位で重複しないよう割り当てる（`blog/seats.py::_assign_emotions`、決定的）。

| order | 席 | 表情 |
|---|---|---|
| 01 | （総括・集合カード） | — |
| 02 | P07 Gemini 3.5 Flash-Lite | 楽（ease・dominant） |
| 03 | P08 GPT-5.6 Luna | 焦（panic・次点。dominantは疑だがP10と衝突するため） |
| 04 | P05 DeepSeek V4 Pro | 楽（ease・dominant） |
| 05 | P11 Claude Sonnet 5 | 焦（panic・次点。dominantは疑だがP10と衝突するため） |
| 06 | P12 Kimi K2.6 | 奸（smirk・dominant） |
| 07 | P10 Claude Haiku 4.5 | 疑（doubt・dominant） |
| 08 | P02 Claude Opus 5 | 奸（smirk・dominant） |
| 09 | P04 GPT-4.1 Mini | 楽（ease・dominant） |
| 10 | P09 DeepSeek V4 Flash | 疑（doubt・dominant） |
| 11 | P03 Grok 4.3 | 楽（ease・dominant） |
| 12 | P01 GPT-5.6 Terra | 疑（doubt・次点。dominantは奸だがP06と衝突するため） |
| 13 | P06 GPT-5.6 Sol | 奸（smirk・dominant） |

同じ会社（OpenAI: P01・P06・P08・P04、Anthropic: P02・P10・P11）の中で表情が重複しないことを
`tests/test_blog_cards.py::test_real_game_seat_table_no_emotion_collision_within_vendor` で機械確認。
