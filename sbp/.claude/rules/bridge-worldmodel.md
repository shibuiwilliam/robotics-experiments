---
paths:
  - "src/sbp/worldmodel/**"
  - "src/sbp/bridge/**"
---
# ワールドモデル層 / 接地層のルール

## WorldModel Protocol（`sbp.worldmodel.base`）
- 全モデル族が `encode / step / rollout / embed / health / attribute` を実装する。戻り値は `LatentState(z, cov | ensemble, level, ts, wm_version)`。
- **`probe` / `explain` を WM に足さない**（ADR-0007）。プローブ ID を受け取ると WM が接地層の型を知ることになり、層の依存（import-linter の契約「worldmodel は記号を決めない」）に反する。帰属は `attribute(state, weights)`（線形汎関数の係数）で、プローブの適用は `bridge`、説明の組み立ては `reasoning.explain` が行う。`health()` の整合残差は呼び出し側から渡す。
- `context`（記号条件付けベクトル）は `sbp.worldmodel.context.build(entity, mask: AuditMask)` でのみ作る。マスクなしの呼び出しを作らない（不変条件 6）。
- モデル族は `src/sbp/worldmodel/families/<name>.py`。初期は `koopman`（線形潜在）と `jepa_ts`（時系列潜在予測）。パラメータ数は数百万以下、MPS で学習できるサイズに収める。
- 学習は `src/sbp/worldmodel/train/`。損失は `L_pred + λ_phys + λ_probe + λ_logic + λ_align` の合成で、各項を個別にログする。`λ_align` は後期に有効化するスケジュールを既定にする。
- レジストリ: `WorldModelVersion` をオントロジーに登録し、重みは `s3://models/<id>/<version>/` に置く（objectstore。実体は SeaweedFS、ADR-0003）。ロードは version 指定必須（`latest` 禁止）。
- `health()` は潜在シフト（MMD / PSI）、予測誤差、整合残差を返す。閾値は Settings。

## プローブ（`sbp.bridge.probes`）
- まず線形プローブ。非線形が必要なら「潜在が線形分離できない」ことをログに残し、WM 改善候補として登録する。
- 登録メタデータ（設計書 §5.2）は pydantic `ProbeSpec` で必須化。`status: candidate | active | deprecated`。
- `promote()` の条件は AND: AUROC ≥ 0.85、ECE ≤ 0.05、整合残差 ≤ 既存 active の中央値。満たさなければ例外。
- 弱ラベルは `TimeBag(concept, entity, interval, weight)`。重みは信頼性モデル由来。MIL 損失は `src/sbp/bridge/mil.py`。
- 出力は必ず `Calibrator.apply` を通し、認識的 / 偶然的不確実性を分けて返す。

## 事象昇格（`sbp.bridge.lifting`）
- 変化点検出は自前の BOCPD（`bocpd.py`）。`ruptures` を使うならオフライン評価に限る（いまは依存に入っていない。入れるなら dev 依存にする）。
- 処理順: 変化点 → ヒステリシス（0.7 / 0.3、Settings）→ 最小持続 → 逆写像（clingo で候補事象型と尤度）→ 重複抑制 → `TypedProbabilisticEvent`。
- 区間には `uncertainty_s` を必ず入れる。点時刻のイベントを出さない。

## 同一性・空間（`sbp.bridge.identity`, `sbp.bridge.spatial`）
- Track はまず `UnidentifiedPhysicalObject` として登録する。束縛は `scipy.optimize.linear_sum_assignment`、コストは `-log p`。多重仮説が必要になったら ADR。
- `IdentityClaim` は撤回可能。撤回時は `Discrepancy(kind=4)` を必ず起票する。
- Person の Track を個人識別に使わない。外観埋め込みを Person に紐づけて保存しない。
- ゾーン包含は shapely。位置は `(mean, cov)` で受け取り包含確率を返す。

## 検証（`sbp.bridge.validate`）
- pyshacl で形状検証。`Violation` → 隔離キュー、`Warning` → 登録して不一致候補。結果はイベントの `validation` フィールドに付ける。
