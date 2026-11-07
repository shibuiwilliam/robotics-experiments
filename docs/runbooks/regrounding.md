# 再接地の実行手順

対象: プローブ・WM・オントロジーの版を上げたとき、または不一致の遡及調査を依頼されたとき。
設計書 §6.8 と `src/sbp/knowledge/regrounding.py` に対応する。

**この手順は第三者（別セッション・別担当）が読んで実行できることを要求する**（ゲート G7 の条件）。

## 0. 前提の確認

```sh
make up                      # Compose（redpanda / postgres / oxigraph / objectstore）
docker compose -f infra/compose.yaml ps   # 4 サービスが running
make check                   # lint + typecheck + ontology-check + 単体テスト
```

ポートが他プロジェクトと衝突する場合は `.env` で退避する（`docs/runbooks/local-setup.md`）。
以降のコマンドは衝突時の例として `SBP_PG_DSN` / `SBP_S3_ENDPOINT` を明示している。

## 1. 何が変わったかを確認する

版は 4 つあり、**独立に上げられない**（設計書 §12.4）。どれが変わったのかで再接地の規模が変わる。

| 変わったもの | 潜在の再計算 | 影響範囲 |
|---|---|---|
| オントロジー版 | 不要 | 新しい概念のプローブだけ |
| プローブ版 | 不要 | そのプローブが判定する概念だけ |
| WM 版 | **必要** | 全プローブ（潜在の意味が変わる） |
| 写像版 | 不要（再接地の対象外） | 業務記録の取り込みをやり直す |

```sh
cat ontology/VERSION
uv run python -c "from sbp.core import vocab; print(vocab.ONTOLOGY_VERSION)"
```

この 2 つが食い違っていたら、再接地の前に `make gen` と `make seed` を行う
（`make ontology-check` が食い違いを検出する）。

## 2. 影響分析（版の互換性行列）

```python
from sbp.knowledge import CompatibilityMatrix

matrix = CompatibilityMatrix()
# 検証済みの組み合わせを登録しておく（運用では永続化する）
matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")

# WM を上げるとき、再検証が必要な組み合わせを洗い出す
affected = matrix.affected_by(world_model="koopman/1.0.0")
print(affected)
```

**未検証の組み合わせを本番に出さない。** `require_verified()` が例外を投げる。

## 3. 計画とコスト見積り

```python
from datetime import timedelta
from sbp.core import vocab
from sbp.core.time import Interval, now
from sbp.knowledge import RegroundingTrigger, plan_regrounding

plan = plan_regrounding(
    trigger=RegroundingTrigger.PROBE_VERSION,
    reason="Running プローブを v2 に昇格（ECE が 0.0064 → 0.0041 に改善）",
    entities=["https://sbp.example/site/siteA/asset/P-101"],
    fluents=[vocab.ent.Running],
    interval=Interval(now() - timedelta(days=7), now()),
    from_version="probe/1.0.0",
    to_version="probe/2.0.0",
)
print(plan.estimate.explain())
```

**`estimate.is_large` が真（見積り 1 時間以上）なら承認を取る。** 期間を区切って複数回に分ける
ことを検討する——途中で止めたジョブは中途半端な状態を残さないが、やり直しのコストがかかる。

無期限の区間（`interval.end is None`）は拒否される。規模が分からないジョブは安全に止められない。

## 4. 実行

実行は `make reground`（`scripts/reground.py`）。潜在索引から潜在を読み、新しい版のプローブで
**潜在 → プローブ → 事象昇格 → 信念更新**を最初から再生し、いまの信念と比べる（ADR-0031）。
**既定は dry-run**（何も書かない）。差分を確認してから `--apply` を付ける。

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # ポート衝突時の例
export SBP_S3_ENDPOINT="http://localhost:19000"
# 1. dry-run（書かない。レポートを保存して読む）
make reground ARGS="--entity <実体 IRI> --fluent <フルーエント IRI> \
  --start 2026-11-01T00:00:00+00:00 --end 2026-11-08T00:00:00+00:00 \
  --probe-id <新しいプローブの IRI> --from-version probe/1.0.0 --to-version probe/2.0.0 \
  --reason '理由' --report /tmp/regrounding.md"
# 2. 差分を確認したら --apply を足して同じコマンドを実行する
```

終了コードが 1 のときは**差分が閾値を超えていて人のレビューが要る**（手順 5）。dry-run でも 1 になりうる。

- 旧文は `tx_to` を閉じるだけで、削除しない（不変条件 2）。新文は `valid_from` を過去のまま `tx_from` を実行時刻にして足す。
- 対象は belief-worker の来歴の文だけ。人の訂正や他の来歴は閉じない。
- 再生はライブと同じ区切り（`SBP_REASONING_BATCH_SIZE`）で行う。区切りを変えて運用していた場合、差分に偽の変化が出る。
- **WM 版を上げたとき**は、先に `make encode` で新版の潜在を作り、`SBP_BRIDGE_WM_VERSION` を新版にしてから実行する。
- 主張（業務記録）は事象ログの業務トピックを最初から読む。保持期間を過ぎて消えている期間は、主張なしで再生される（差分に出る）。
- 実行記録の代わりに統合テストも同じ経路を通る: `uv run pytest tests/integration/test_reground_e2e.py -m integration`。

## 5. 差分レポートの確認

```python
from sbp.knowledge import summarise_regrounding

report = summarise_regrounding(
    plan, diffs=diffs, reassessed=reassessed,
    started_at=started, finished_at=finished, closed_assertions=closed,
)
print(report.markdown())
```

レポートは次を必ず含む。

- 版の変更（`from → to`）と理由
- 見積りと実績（所要時間、再評価した文の数）
- **閾値を超えた差分の一覧**（既定は確信度 0.1 以上の変化）
- 遡及的に増えた文・消えた文の数
- 不一致の新規起票・消滅
- 旧文の扱い（`tx_to` を閉じた件数。**削除は 0 件**）

**`needs_review` が真なら人が判断する。** 確信度が大きく動いたのは
「モデルが良くなった」のか「新版が壊れている」のか——自動で採用しない。

判断の目安:

| 兆候 | 解釈 |
|---|---|
| 確信度が一方向に大きく動き、真値の分かる区間で正解に近づいた | 改善 |
| 新規の文が大量に出た | プローブが過剰に発火している可能性。ECE を確認する |
| 文が大量に消えた | プローブが発火しなくなった。AUROC と整合残差を確認する |
| 不一致が急に減った | **記録パスと推定パスの循環汚染の兆候**（設計書 §15）。監査隔離を確認する |

## 6. as-of で当時の信念を確認する

再接地の目的は「過去を書き換える」ことではない。**当時の信念が再構成できること**を確認する。

```python
# 再接地前の取引時刻 → 旧版の信念
before = store.query(subject=entity, predicate=fluent, as_of=reground_at - timedelta(seconds=1))
# 再接地後の取引時刻 → 新版の信念
after = store.query(subject=entity, predicate=fluent, as_of=reground_at + timedelta(seconds=1))
```

両方が返ること、かつ `valid_from` が同じで `tx_from` が違うことを確認する。これが成り立たなければ
再接地は失敗している（旧文を消してしまった可能性がある）。

## 7. 記録

- 差分レポートの markdown を `docs/runbooks/` 配下の実行記録として保存する
- 互換性行列に新しい組み合わせを登録する
- `docs/PROGRESS.md` に所要時間と差分の規模を追記する（次回の見積りの材料になる）

## 失敗時

| 症状 | 対処 |
|---|---|
| `RegroundingError: bounded interval` | 区間に終端を入れる |
| `RegroundingError: must state why it runs` | `reason` を書く（レポートの判断材料になる） |
| `RegroundingError: has not been verified` | 昇格パイプライン（影響分析 → カナリア → 再接地 → 差分レビュー）を先に通す |
| 途中で止まった | 旧文は閉じられていないので**再実行して問題ない**（追記専用なので冪等ではないが、重複した文は `tx_from` が違うだけで害がない）。重複が気になる場合は差分レポートで確認する |
| `make seed` が失敗する | Compose のポートを確認（`docker compose ps`）。`SBP_PG_DSN` の退避を忘れていないか |
