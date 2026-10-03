# バックアップとリストア

対象: 知識グラフ（Oxigraph）、投影・潜在索引（TimescaleDB）、モデル重み（SeaweedFS）、
イベントログ（Redpanda）。

**何を失ってよいかが層ごとに違う。**

| 層 | 失ったときの影響 | 復旧元 |
|---|---|---|
| 知識グラフ（信念） | **取り返せない**。アサーションは追記専用の一次データ | バックアップのみ |
| イベントログ | 保持期間内なら再処理できる | ログそのもの（投影は作り直せる） |
| 投影・潜在索引 | 作り直せる（イベントログと潜在から） | 再接地・再投影 |
| モデル重み | 再学習できるが時間がかかる | オブジェクトストア |

したがって**優先順位は 知識グラフ > モデル重み > イベントログ > 投影** である。

## バックアップ

### 知識グラフ

```sh
# 名前付きグラフごとに N-Quads で取る（グラフの境界が保たれる）
curl -s "http://localhost:7878/store" -H "Accept: application/n-quads" > backup/graph-$(date +%Y%m%d).nq
wc -l backup/graph-*.nq    # 行数が前回から減っていたら異常（追記専用なので増えるだけ）
```

**行数が減っていたら止める。** アサーションは追記専用（不変条件 2）なので、減ることは無い。
減っているなら削除が起きている——バックアップより先に原因を調べる。

### TimescaleDB（投影・潜在索引・メトリクス）

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # ポート衝突時の例
docker compose -f infra/compose.yaml exec -T postgres \
  pg_dump -U sbp -Fc sbp > backup/pg-$(date +%Y%m%d).dump
```

潜在索引は大きい。容量が問題なら**潜在索引を除いて**取る（再接地で作り直せる）。

```sh
docker compose -f infra/compose.yaml exec -T postgres \
  pg_dump -U sbp -Fc --exclude-table-data='latent_index*' sbp > backup/pg-light-$(date +%Y%m%d).dump
```

ただし `latent_index_registry` は**除外しない**（どの版がどの表を使うかの対応が失われると、
書き込み先が決まらなくなる）。

### モデル重み

```sh
export SBP_S3_ENDPOINT="http://localhost:19000"
# s3://models/<model_id>/<version>/ 配下
uv run python -c "
import boto3
from sbp.core.config import Settings
s = Settings()
client = boto3.client('s3', endpoint_url=s.s3_endpoint,
                      aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key)
print([o['Key'] for o in client.list_objects_v2(Bucket='models').get('Contents', [])])
"
```

### オントロジー

Git にあるので別途のバックアップは不要。**ただしストアに投入された版と Git の版が一致している
ことを確認する**（`make ontology-check` の版検査）。

## リストア

### 順序

1. Compose を起動する（`make up`）
2. **オントロジーを投入する**（`make seed`）——信念の復元より先。版が合っていないと
   `ontology_version` の照合が狂う
3. 知識グラフを復元する
4. TimescaleDB を復元する
5. モデル重みを復元する
6. 検証する

### 知識グラフ

```sh
curl -X POST "http://localhost:7878/store" \
  -H "Content-Type: application/n-quads" --data-binary @backup/graph-YYYYMMDD.nq
```

### TimescaleDB

```sh
docker compose -f infra/compose.yaml exec -T postgres \
  pg_restore -U sbp -d sbp --clean --if-exists < backup/pg-YYYYMMDD.dump
# 潜在索引を除いて取った場合は、再接地で作り直す（docs/runbooks/regrounding.md）
```

### 検証

```sh
make check
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp" SBP_S3_ENDPOINT="http://localhost:19000"
uv run pytest tests/integration -m integration     # G0〜G6 が通ること
```

**as-of クエリで過去の信念が返ることを必ず確認する。** これが返らないリストアは失敗である
（二時制の履歴が失われている）。

```python
rows = store.query(subject=entity, predicate=fluent, as_of=some_past_time)
assert rows, "過去の信念が復元できていない"
```

## 定期的に確認すること

- バックアップから**実際にリストアできるか**（取れているだけでは意味がない）
- 知識グラフの行数が単調増加しているか（減少は削除の兆候）
- `latent_index_registry` がバックアップに含まれているか
