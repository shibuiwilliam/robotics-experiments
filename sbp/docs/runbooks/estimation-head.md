# 推定パスの先頭（エンコードジョブ）の運用

ADR-0023。現場のエッジが置いた観測窓（JSONL スプール）から Koopman を学習し、潜在を `latent_index` へ書く。
観測値そのものは永続化しない（不変条件 9）。

## 前提
- Compose 起動済み（postgres, objectstore）、`make seed` 済み。
- `.env` に `SBP_WM_SPOOL_DIR`（スプールの置き場）と `SBP_BRIDGE_WM_VERSION`（`<モデル IRI>/<版>`）がある。既定値は無い。

## 1. 学習（版ごとに 1 回。同じ版へ上書きしない）
```
make train-wm SEED=1
```
- 重みは `models/<モデル>/<版>/koopman.npz` に置かれる。同じ版が既にあれば失敗する。直したいときは新しい版を切る。
- スプールが空・存在しない・チャンネルが食い違うときは `ConfigError` で止まる。

## 2. エンコード
```
make encode            # 1 回
uv run python -m scripts.encode --watch 30   # 30 秒ごと
```
- 実体ごとのウォーターマークで冪等。書いた後に進めるので、途中で落ちても同じ窓を再度書かない。
- 出力の `EncodeReport`: `rows_written` / `skipped_processed` / `skipped_short` / `skipped_mismatch` / `rejected` / `health`。
  `rejected` が増えたらスプールの不正な行（`file:line: reason`）を現場へ返す。

## 失敗時
| 症状 | 原因 | 対処 |
|---|---|---|
| `no spool directory` | `SBP_WM_SPOOL_DIR` 未設定 | `.env` に設定 |
| `SBP_BRIDGE_WM_VERSION` エラー | 版未設定 | 設定して再実行 |
| 重みが見つからない | 学習前 | `make train-wm` |
| `ArtifactImmutableError` | 同じ版の再学習 | 新しい版を切る |

## 3. プローブの学習（接地パス。ADR-0024）
前提: 上のエンコードで潜在が索引にあり、業務の主張イベント（`BusinessEvent/<フルーエント>`）が
`<site>.integration.business` に流れている。
```
make train-probe FLUENT=<フルーエント型 IRI> PROBE_ID=<プローブ IRI（版を含む）> SEED=1
```
- 昇格ゲート（AUROC・ECE・整合残差）を通れば束が置き場に置かれ、`SBP_BRIDGE_PROBE_IDS` に書いて bridge-worker を起動できる。
- 通らなければ**何も置かれず**終了コード 1。`probe_auroc` / `probe_ece` は記録される。ログの `reasons` を見る。
- 「各枠の標本が足りない」は弱ラベルの不足。主張イベントが溜まるまで待つか `SBP_BRIDGE_PROBE_MIN_LABELLED` を見直す（緩めるときは ADR）。
- 同じ版は上書きされない（`ArtifactImmutableError`）。学び直しは新しい版の `PROBE_ID` で。
