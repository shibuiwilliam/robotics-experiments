# ADR-0007: `WorldModel` Protocol から `probe` / `explain` を外し、帰属は記号を知らない形で返す

- 日付: 2026-10-03
- 状態: 採択
- 触る層: worldmodel（Protocol）、bridge（プローブ適用側）

## 文脈

設計書 §4.3 は `WorldModel` の共通インタフェースを次のように定めている。

```text
interface WorldModel {
  encode(obsWindow, context?)  -> LatentState
  step(z, action?, context?)   -> LatentState
  rollout(...)                 -> Trajectory[]
  probe(z, probeIds[])         -> { probeId: Distribution }
  explain(z, probeId)          -> Attribution
  embed(z, level)              -> Vector
  health()                     -> { latentShift, predError, alignResidual }
}
```

このうち `probe` と `explain` は**プローブ ID を引数に取る**。M4-01 でこれをそのまま実装しようとして、三つの規則と衝突することが分かった。

1. **CLAUDE.md の層依存**: `core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge`。`worldmodel` は `bridge` を import できない。プローブの型（`ProbeSpec`, `LinearProbe`, `Calibrator`）はすべて `bridge` にある。
2. **CLAUDE.md の層の責務**: 「`worldmodel` は記号を決めない（プローブは `bridge`）」。
3. **`.claude/rules/bridge-worldmodel.md`**: 「出力は必ず `Calibrator.apply` を通し、認識的／偶然的不確実性を分けて返す」。較正器はプローブに付属し、昇格ゲート（AUROC ∧ ECE ∧ 整合残差）もプローブ側にある。

`probe()` を worldmodel に置くには、(a) プローブ型を `core` に降ろす、(b) `worldmodel` が `bridge` を import する、(c) プローブを呼び出し可能オブジェクトとして注入する、のいずれかが必要になる。

## 選択肢

1. **Protocol から `probe` / `explain` を外し、帰属は「線形汎関数に対する潜在次元の寄与」として返す。** プローブの適用は `bridge` が `LatentState` を受け取って行う。
   - 利点: 層依存をそのまま守れる。モデル族はプローブの存在を知らないまま実装でき、プローブの追加・昇格・廃止が WM に影響しない。帰属（どの潜在次元が効いたか）は WM にしか計算できない情報なので、記号を知らない形（重みベクトル）で受け取れば WM の責務に収まる。
   - 欠点: 設計書 §4.3 の記述から外れる。呼び出し側が 2 段（`wm.encode` → `probe.predict`）になる。
2. プローブ型を `core` に降ろす。
   - 利点: 設計書の記述どおりに `probe()` を置ける。
   - 欠点: `core` は「型と最小のユーティリティ」であり、較正器・昇格ゲート・MIL 損失を含む接地の実装がそこに来る。`core` が numpy と学習コードに依存し、層の意味が崩れる。昇格ゲートが `core` にあると、`bridge` を通さずにプローブを active にする経路ができる。
3. プローブを呼び出し可能オブジェクトとして WM に注入する（`register_probe(probe_id, callable)`）。
   - 利点: 設計書の呼び出し形（`wm.probe(z, ids)`）を保てる。型依存も発生しない。
   - 欠点: WM が「どのプローブが登録されているか」という状態を持つ。同じ潜在に対して、登録状態によって違う結果を返すモデルになり、「モデル版 + 潜在 → 出力」の再現性が崩れる（設計書 §8.8 の as-of 説明が成立しない）。登録漏れが静かな欠落になる。

## 決定

**選択肢 1 を採る。**

`sbp.worldmodel.base.WorldModel` Protocol のメソッドは `encode / step / rollout / embed / health / attribute` とする。`probe` と `explain` は置かない。

- プローブの適用は `bridge` が行う。`LinearProbe.predict(latents)` が `LatentState.z`（またはアンサンブル）を受け取る形に揃える。
- 帰属は `attribute(state, weights) -> Attribution` とする。`weights` は線形汎関数の係数（線形プローブの重みがそのまま入る）で、WM はそれが何を表す記号なのかを知らない。返す `Attribution` は潜在次元・モダリティ・観測窓への寄与を持つ。
- 設計書 §4.3 の意図（「潜在から概念への出力と、その帰属を取れる」）は、呼び出し側の 2 段構成で満たす。説明サービス（`sbp.reasoning.explain`）が受け取る `LatentSide` は、プローブ ID と出力、WM 版、帰属をすべて持つため、**利用者から見た説明の情報量は変わらない**。

## 帰結

- モデル族（`koopman`, `jepa_ts`）はプローブを一切知らずに実装・テストできる。G4 の「2 モデル族が同一 Protocol で差し替え可能」は、プローブとは独立に証明できる。
- 「プローブが WM に埋め込まれていないか」は import 境界の lint（import-linter）で機械検査できる。`worldmodel` → `bridge` の import が増えたら落ちる。
- 設計書 §4.3 のインタフェース記述は本 ADR で更新されたものとして読む。設計書本体の該当箇所に ADR への参照を入れる。
- `Attribution` 型は `worldmodel` に置く。`bridge` が `probe_id` を添えて使う。

## 未決

- **非線形プローブの帰属**: `attribute()` は線形汎関数を前提にしている。非線形プローブ（`.claude/rules/bridge-worldmodel.md` は「潜在が線形分離できないことをログに残してから」使うと定めている）が必要になった時点で、勾配ベースの帰属を受け取る形に拡張するか、別メソッドにするかを決める。
