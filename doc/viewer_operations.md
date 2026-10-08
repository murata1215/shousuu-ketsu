# Viewer運用マニュアル

嘘八百万 —少数決— 観戦ビューアの常駐・再起動・確認手順。dangou-card・
gentei-jankenの`doc/viewer_operations.md`と同じ方式（systemd user service）。

## 現行構成

| 項目 | 現行値 |
|---|---|
| 常駐方式 | `uso8m` ユーザーのsystemd user service |
| service名 | `shousuu-viewer.service` |
| unit | `~/.config/systemd/user/shousuu-viewer.service` |
| working directory | `/home/uso8m/shousuu-ketsu` |
| ExecStart | `/home/uso8m/shousuu-ketsu/.venv/bin/python -c "from viewer.server import main; main()"` |
| Viewer bind | `127.0.0.1:9028` |
| 公開経路（人間の作業が必要） | Caddy: `shousuu-ketsu-viewer.devrelay.io` → `localhost:9028`（未設定） |

unitは`Restart=always`、`RestartSec=3`で動作する。

## 通常の再起動と確認

```bash
# 1. 現在の状態
systemctl --user status shousuu-viewer.service --no-pager

# 2. 再起動
systemctl --user restart shousuu-viewer.service

# 3. 再起動後の状態
systemctl --user status shousuu-viewer.service --no-pager

# 4. Viewer自身へのローカル疎通
curl -fsS http://127.0.0.1:9028/api/games
```

ログ確認:

```bash
journalctl --user -u shousuu-viewer.service -n 100 --no-pager
journalctl --user -u shousuu-viewer.service -f
```

## Caddyでの公開（人間が実行する手順。sudo必須のためこのサイクルでは未実施）

`/etc/caddy/sites.d/shousuu-ketsu-viewer.devrelay.io` に以下を新規作成する
（dangou-card-viewer.devrelay.io の設定に倣う）:

```caddyfile
shousuu-ketsu-viewer.devrelay.io {
  import sites_access_log
  reverse_proxy localhost:9028
  handle_errors {
    rewrite * /index.html
    root * /home/devrelay/testflight/shousuu-ketsu-viewer/placeholder
    file_server
  }
}
```

その後:

```bash
sudo systemctl reload caddy
```

DNS・TLS証明書の発行がまだであれば、その設定も人間側で確認する。

## トラブルシューティング

| 症状 | 確認順序 |
|---|---|
| serviceが`inactive`／`failed` | `status`、`journalctl`で原因を確認してから`restart`。 |
| 9028へのcurlが失敗 | service statusとjournalを確認し、`127.0.0.1:9028`のbind設定・Python起動例外を調べる。 |
| ローカルcurlは成功するが公開URLだけ失敗 | ViewerではなくCaddy経路を疑う（DNS・TLS・upstream設定）。 |

## 旧方式との区別

`nohup`でのバックグラウンド常駐や`viewer.pid`管理は、このプロジェクトでは最初から
採用していない（dangou-card/gentei-jankenのsystemd移行後の方式に最初から合わせた）。
