# devlog INDEX

devlog本文は `doc/devlog/YYYY-MM-DD_HHMMSS.md` に1サイクル1ファイルで保存する。
このINDEXには1行だけ追記し、`doc/devlog/` の一括catは禁止。

## 索引

- 2026-10-06_073837 | サイクル0.1 | 流用調査 | dangou-card/gentei-jankenの部品表（約35項目）・流用方式・優劣・仕様書あいまい点16件・段取り案をdoc/analysis/reuse_investigation.mdにまとめた（実装なし、読み取りのみ）
- 2026-10-06_170209 | サイクル0.2 | 基盤づくり | git init+remote・uv環境・gentei-jankenからAI呼び出し層15ファイル約2,400行をコピー（DevRelay宛先cuidは既定値廃止し環境変数必須化）、テスト41件全PASS、1コミットでmainへpush
- 2026-10-07_041047 | サイクル1.0 | 動く骨格 | engine/13+bots/4+scripts/1+tests/8ファイル計2,673行を新規作成し、契約なしで12ラウンドの投票・配当・持ち越し・2種債務を完走。テスト76件全PASS（§12.3の#1,2,3,4,5,6,7,10,15,16,23,24含む）、無作為100試合で帳尻/現金非負/借金上限を確認、利息は整数切り上げ除算で実測値と一致
