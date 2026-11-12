# What-if の実行手順（M10-03、ADR-0036）

対象: 「このアクションを印加したら、フルーエントの成立確率はどう動くか」を問うとき。
`make whatif` が介入ありの rollout を介入なしの rollout と並べ、差を返す。

## 0. 前提 — 介入付きで学習した WM であること

**介入付きでない WM は拒否する**（終了コード 2、理由をログに出す）。介入なしの rollout を What-if として返す経路は無い
（介入の効果が常に 0 になる）。

1. 現場のエッジが、観測窓のスプール（JSONL）に任意の `actions` を足す: 時刻と同じ行数 × 介入次元（有限）。
   `{"entity": …, "channels": […], "timestamps": […], "values": [[…]], "actions": [[…]]}`
2. `make train-wm`（`scripts.train_koopman`）で学習し直す。介入は**全行にあるか全行に無いか**のどちらか
   （一部の行にしか無いと ConfigError で拒否する）。
3. 新しい WM 版で `make encode` → プローブの学習（`scripts.train_probe`）→ 昇格、までを通常どおり行う。

介入チャンネルは「`actions` の列番号」で、**アクション型との対応は呼び出し側が与える**（`--action-type` の IRI と `--channel`）。

## 1. 実行

```sh
make whatif ARGS='--entity <実体 IRI> --fluent <フルーエント IRI> --probe-id <プローブ IRI> \
  --action-type <アクション型 IRI> --channel 0 --magnitude 1.0 --horizon 12 --seed 7'
```

- 終了コード: 0 成功 / 3 設定の誤り（プローブと WM の版の不一致など）/ 1 潜在が無い・古い（`SBP_REASONING_FORECAST_MAX_ORIGIN_AGE_S`）/ 2 拒否（介入付きでない WM・範囲外のチャンネル・プローブが無いフルーエント）。
- 同じ問い（実体・フルーエント・WM 版・起点・アクション型・チャンネル・強さ・地平）は 2 度書かない。

## 2. 結果の読み方

- 曲線は GraphQL `scenarios`（`hypothetical` の許可が要る。既定の要求者には見えない）で読む。`baseline`（介入なし）、`intervened`（介入あり）、`delta`（差）。
- 結果の文は `urn:sbp:scenario:<id>` に `hypothetical` で書かれ、**主信念の問い合わせに混ざらない**。主信念への昇格 `promote_scenario` の入口は無い。
- **因果の主張として使わない。** 効果は WM の線形な制御項から出る。介入を観測した件数（妥当性）は数えていない（`ActionEvent` を永続化していない。H-07）。
  計画差分の What-if は、条件付けが学習されていないため入口が無い。
