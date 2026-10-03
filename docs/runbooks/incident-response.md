# 障害対応

対象: ブローカー停止、グラフストア停止、ワーカー滞留。設計書 §12 と `services/runtime.py` に対応する。

**基本方針: 止まったことを隠さない。** SBP は信念を管理する基盤であり、
「動いているが観測していない」状態が最も危険である（不一致が出ないので正常に見える）。
疎通が失われたらワーカーは起動せず、既存のワーカーは停止要求で落ちる。

## 共通の初動

```sh
docker compose -f infra/compose.yaml ps        # 4 サービスの状態
docker compose -f infra/compose.yaml logs --tail=50 <service>
```

`oxigraph` の Health 列が空なのは**正常**（distroless で healthcheck を書けない。ADR-0004）。
疎通はアプリ側が起動時に検査する。

## 1. ブローカー（Redpanda）停止

### 症状

- ワーカーが起動時に `DependencyUnavailableError: ... redpanda unreachable` で落ちる
- 稼働中のワーカーが消費を止め、`event_log_lag` が増え続ける

### 対処

```sh
docker compose -f infra/compose.yaml restart redpanda
docker compose -f infra/compose.yaml logs --tail=100 redpanda | grep -i error
```

### 復旧後に確認すること

**イベントは消えていない**（at-least-once）。ワーカーは最後にコミットしたオフセットから再開し、
処理済みのイベントは `processed_events(event_id)` で冪等に弾かれる（不変条件 4）。

```sh
# 滞留が解消に向かっているか
uv run python -c "print('topics:'); " # scripts/topics.py の一覧で確認
```

**再処理による二重の信念を疑う必要はない。** アサーションは `event_id` で冪等化されており、
同じイベントから同じ文が 2 つ出ることはない。

## 2. グラフストア（Oxigraph）停止

### 症状

- `DependencyUnavailableError: ... oxigraph unreachable`
- API の `/health` が `store_reachable: false`
- GraphQL が `storeReachable: false` を返す

### 対処

```sh
docker compose -f infra/compose.yaml restart oxigraph
# 起動は RocksDB のリカバリを含むので数秒かかる
sleep 5
curl -s "http://localhost:7878/query?query=ASK%20%7B%7D"
```

### 復旧後に確認すること

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # ポート衝突時の例
make seed          # オントロジーが入っているか（版が合っているか）
uv run pytest tests/integration/test_g0_roundtrip.py -m integration
```

**オントロジーの版を必ず確認する。** ストアが古い版のままだと、`as_of` の照合が狂う
（`make ontology-check` が `owl:versionInfo` と `VERSION` の食い違いを検出する）。

### データが壊れた疑いがあるとき

RocksDB のロックが残って起動しない場合、ボリュームを消す前に**必ず**次を確認する。

```sh
docker compose -f infra/compose.yaml logs oxigraph | tail -50
```

ボリューム削除（`make nuke`）は**信念をすべて失う**。オントロジーは `make seed` で戻るが、
アサーションは戻らない。削除する前に責任者の承認を取る。

## 3. ワーカー滞留

### 症状

- `event_log_lag` が増え続ける（`sbp_operations_event_log_lag_seconds`）
- 不一致の起票が止まっている
- ワーカーのログに `worker.stopped` が出ていない（= 生きているが進んでいない）

### 切り分け

```sh
# ワーカーが生きているか
ps aux | grep -i worker
# どこで止まっているか（structlog の最後のイベント）
# worker.coupling_cycle が周期どおり出ているか
```

| 最後に出ているログ | 解釈 | 対処 |
|---|---|---|
| `worker.dependency_ready` のみ | 疎通後に 1 件も処理していない | ブローカーのトピックとオフセットを確認 |
| `worker.coupling_cycle` が出続ける | 結合は回っているがイベントが来ていない | 上流（接地層・統合層）の停止を疑う |
| 何も出ない | ループが外部呼び出しで固まっている | SIGTERM を送る（graceful shutdown。処理中のイベントを捨てない） |

```sh
# graceful shutdown（いまのループを終えてからオフセットをコミットして終了する）
kill -TERM <pid>
```

**`kill -9` を使わない。** 処理中のイベントが捨てられ、オフセットだけが進む可能性がある
（at-least-once が壊れ、イベントが 1 件消える）。

## 4. 書き戻しの異常

### 症状

業務システムに意図しない変更が入った、または変更が入らない。

### 確認

```sh
# writeback のモードが dry のままか（既定は dry）
uv run python -c "from sbp.core.config import Settings; print(Settings().writeback_mode)"
```

**既定は `dry`。** `live` で動いていた場合、各コマンドには `compensation`（補償アクション）が
宣言されている（宣言の無いアクション型は登録できない）。補償は `ActionRequest.compensate()` を
経て別の `ActionRequest` として実行する——補償にも前提と承認がある。

## 5. 報告に必ず含めること

- 症状と最初に気付いた時刻（`event_log_lag` のグラフが有用）
- 停止していた期間（**この期間の観測は「無い」のではなく「していない」**。
  信念が更新されていないだけで、不一致が無かったわけではない）
- 復旧後に再処理されたイベント数
- 停止期間中に出なかった不一致の遡及調査が必要か（必要なら
  `docs/runbooks/regrounding.md` の `RETROSPECTIVE_INVESTIGATION` で再接地する）
