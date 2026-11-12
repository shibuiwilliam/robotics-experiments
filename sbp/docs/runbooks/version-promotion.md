# 版の昇格

対象: オントロジー版・WM 版・プローブ版・写像版のいずれかを上げるとき。
設計書 §12.4（統合ライフサイクル）と `src/sbp/worldmodel/registry.py`、
`src/sbp/knowledge/regrounding.py` に対応する。

**4 つの版は独立に上げられない。** プローブは特定の WM 版の潜在に対して学習されており、
WM 版はオントロジーの概念に紐づき、写像は記録型に紐づく。どれを上げても同じパイプラインを通す。

## パイプライン（設計書 §12.4）

```
影響分析 → カナリア → 再接地 → 差分レビュー
```

飛ばせる段階は無い。特に**カナリアを飛ばして全体に出さない**
（`ModelRegistry.promote` が `candidate → active` の直行を拒否する）。

## 1. 影響分析

```python
from sbp.knowledge import CompatibilityMatrix

matrix = CompatibilityMatrix()
# 既存の検証済み組み合わせ（運用では永続化する）
matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")

# 上げる版を含む組み合わせを洗い出す
print(matrix.affected_by(world_model="koopman/1.0.0"))
```

洗い出した組み合わせすべてについて、次の段階で再検証する。

## 2. オントロジー版を上げる場合

```sh
# 1. TTL を編集（ontology/core/*.ttl, ontology/domain/*.ttl）
# 2. CHANGELOG に semver 判定を書く（MAJOR は ADR と再接地計画が必須）
# 3. VERSION と owl:versionInfo の両方を更新する
#    （ontology-check が食い違いを検出する）
make gen             # vocab.py と schemas/ を再生成
make ontology-check  # 7 項目 + 版の一致
make check           # 後方互換を契約テストが検査する
```

**生成差分は別コミットにする**（CLAUDE.md）。`vocab.py` と `schemas/` は生成物である。

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # ポート衝突時の例
make seed            # ストアに新しい版を投入する
uv run pytest tests/integration -m integration   # 全件（G0〜G7 と M8 の統合テスト）が通ること
```

**`make seed` を忘れると G0/G1/G2 の版検査が落ちる。** ストアだけが古い版を名乗る状態になる。

## 3. WM 版・プローブ版を上げる場合

```python
from sbp.worldmodel import CompatibilityEntry, ModelRegistry, ModelStatus

registry = ModelRegistry()
entry = registry.register(new_model.meta)       # candidate として登録される

# プローブとの互換性を評価して記録する（**次元が同じでも潜在の意味は同じではない**）
entry.record_compatibility(CompatibilityEntry(
    probe_id="probe:running", auroc=measured_auroc, ece=measured_ece,
    align_residual=measured_residual, evaluated_at="2026-10-03T00:00:00Z",
))

# カナリア（一部拠点・一部概念）
registry.promote(model_id, version, ModelStatus.CANARY,
                 required_probes=["probe:running"],
                 min_auroc=0.85, max_ece=0.05, max_residual=0.2)
```

未評価のプローブがあると昇格は拒否される。ゲートは **AUROC ∧ ECE ∧ 整合残差の AND**
（静的精度だけで active にしない。CLAUDE.md の禁止事項）。

カナリアで観察する指標:

| 指標 | 見るもの |
|---|---|
| `sbp_worldmodel_latent_shift_ratio` | 潜在分布が学習時から離れていないか |
| `sbp_worldmodel_pred_error_ratio` | 予測誤差が悪化していないか |
| `sbp_grounding_probe_ece_ratio` | **較正が崩れていないか**（部分的な崩壊は AUROC に出ず ECE に出る） |
| `sbp_reconciliation_discrepancies_raised_total` | 起票が急増・急減していないか |

```python
# 全体へ
registry.promote(model_id, version, ModelStatus.ACTIVE,
                 required_probes=["probe:running"],
                 min_auroc=0.85, max_ece=0.05, max_residual=0.2)
# 旧版は退役させる（**ロード可能なまま残す**。過去の推定の説明に必要）
registry.promote(old_model_id, old_version, ModelStatus.DEPRECATED,
                 min_auroc=0.0, max_ece=1.0, max_residual=1.0)
```

## 4. 再接地

`docs/runbooks/regrounding.md` に従う。WM 版の昇格では**潜在そのものを作り直す**
（`RegroundingTrigger.WORLD_MODEL_VERSION.invalidates_latents` が真）ため、
プローブ版の昇格に比べて桁違いに重い。見積りを必ず確認する。

潜在の次元が変わる場合は**新しいテーブルが必要**になる。
`latent_index_registry` に登録されていない次元は書き込みが成立しない
（アプリは実行時に表を作らない。alembic でマイグレーションを切る）。

## 5. 差分レビュー

再接地の差分レポート（`report.markdown()`）を人が確認する。
`needs_review` が真なら採用を保留し、判断の根拠を記録する。

## 6. 記録

```python
matrix.record(ontology="0.5.0", world_model="koopman/2.0.0", probe="2.0.0", mapping="1.0.0")
```

**登録を忘れると、次回の昇格で `require_verified` が通らない。** これは意図した摩擦である
（検証したことの記録が無い組み合わせを本番に出さない）。

## ロールバック

旧版は `deprecated` でもロード可能なまま残っている。

1. 旧版を `canary` → `active` に戻す（`deprecated` からは直接戻せないので、再登録ではなく
   **新しい版として登録する**——版を巻き戻すと、どの重みで出した信念かが曖昧になる）
2. 新版で追加されたアサーションは `tx_to` を閉じる（削除しない）
3. 差分レポートに「ロールバックした理由」を追記する

**過去の信念は消さない。** `as_of` で「新版を使っていた期間の信念」も再構成できる状態を保つ。
