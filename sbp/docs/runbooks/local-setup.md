# runbook: ローカル環境の立ち上げ

対象: 開発機（macOS / Apple Silicon）で SBP の Compose スタックを動かす。

## 手順

```sh
make bootstrap   # uv sync → .env 生成 → イメージ取得
make up          # 4 サービス起動
make ps          # healthy になるまで確認（初回は 30〜60 秒）
make seed        # トピック・マイグレーション・バケット
make test-int    # 統合テスト
```

## サービス構成（設計書 §12、ADR-0003）

| サービス | イメージ（固定版） | 既定ポート | 役割 |
|---|---|---|---|
| redpanda | `redpandadata/redpanda:v24.2.7` | 9092 / 9644 | イベントログ（Kafka API） |
| postgres | `timescale/timescaledb-ha:pg16.15-ts2.30.2` | 5432 | 時系列・潜在索引・投影 |
| oxigraph | `oxigraph/oxigraph:0.5.11` | 7878 | 知識グラフ（SPARQL 1.1 + RDF 1.2 の triple term。ADR-0005） |
| objectstore | `chrislusf/seaweedfs:3.80` | 9000 | raw 層・モデル重み（S3 互換） |

メモリ上限の合計は 6 GB（2+2+1+1）。Docker Desktop / OrbStack の上限もこれに合わせる。

**oxigraph の Health 列は空欄が正常**（ADR-0004）。distroless イメージでコンテナ内から検査が
できないため healthcheck を付けていない。疎通は統合テストの fixture とワーカー起動時に
SPARQL で確認する。空欄を異常と誤認しないこと。

## よくある失敗

### ポートが既に使われている

```
Error response from daemon: ... Bind for 0.0.0.0:9000 failed: port is already allocated
```

同じ開発機の他プロジェクトが 5432（Postgres）や 9000（S3 互換ストア）を使っていると起きる。
`.env` で退避させる（`.env.example` の「ポート」節のコメントを外して値を変える）。

```sh
SBP_PORT_POSTGRES=15432
SBP_PORT_S3=19000
```

退避したら、同じ `.env` の接続文字列も合わせる。

```sh
SBP_PG_DSN=postgresql://sbp:sbp@localhost:15432/sbp
SBP_S3_ENDPOINT=http://localhost:19000
```

どのプロセスが握っているかは `lsof -nP -iTCP:9000 -sTCP:LISTEN` で確認できる。

`make up` は直下の `.env` を `--env-file` で compose に渡す。**2026-10-25（X-14）より前は渡しておらず、
`.env` のポート退避は compose に効いていなかった**（compose は compose ファイルのディレクトリ `infra/` の
`.env` しか読まない）。古い手順で `.envrc` に退避を書いている開発機は、`.env` に移してよい。
`make` を経ずに `docker compose` を直接叩くときは `--env-file .env` を付ける。

### イメージが取得できない

`pull access denied` / `unauthorized` が出たら、そのイメージが匿名取得できなくなっている可能性がある
（MinIO で実際に起きた。ADR-0003 を参照）。レジストリ API で確認し、代替を ADR で決める。

### healthcheck が healthy にならない

`make logs` で該当サービスのログを見る。redpanda は起動に 20 秒程度、postgres は初回の
初期化に 30 秒程度かかる。`start_period` を越えても unhealthy なら、`mem_limit` に対して
Docker の割り当てメモリが足りていないことが多い。

## 片付け

```sh
make down    # コンテナ停止（データは残る）
make nuke    # ボリュームも削除（データが消える。唯一の削除経路）
```
