# EXP-08 概念発見

出典：`docs/poc_plan.md` 6.1・6.2（一字一句転記、要約ではない）。

## 検証仮説

H7（3.1）：未知の概念を残差から発見できる。

- 主指標：注入概念の候補上位5への出現率
- 目標値：3種中2種以上
- 段階：P2

## 手順（6.2 原文）

新しい荷姿（例：長尺物）、新しい工程（例：再検品）、新しい一時置き場の運用を1種ずつ、
オントロジーには未登録のまま導入。2週間後に残差クラスタから候補を生成し、盲検の評価者が
上位5候補を判定。

## 出力（6.2 原文）

出現率、候補の説明の妥当性（評価者3名の一致率）。

## このセッションでの実装範囲・簡略化

- 3種の注入は `src/gtwm/sim/concept_injection.py`：
  - `oversized_cargo_proxy`：既存倉庫に元からある3段積みケースの上段（未登録の荷姿の代理。
    捏造した新形状ではなく、実際に他と異なる物理的footprintを持つ既存の個体を使う）
  - `reinspecting_step`：検品記録の後に確率的に追加される、オントロジー未登録の新CBV
    bizStep（"reinspecting"）
  - `staging_overflow`：本来滞留を想定しないゾーンに長時間留まる個体の事後タグ付け
- 「2週間運用」ではなく `config.n_episodes`（smoke既定2）本の30秒エピソード。
- 概念発見自体（残差収集→HDBSCAN→フィルタ→LLM命名→`gt:ConceptCandidate`保存）は
  `src/gtwm/grounding/concept_discovery.py` を使う。LLM呼出はsmoke/テストでは
  `configs/llm_mock.yaml`（mock、無課金）。
- 「出現率」の代理指標：候補クラスタは個体永続IDを持たない生の残差（フレーム単位の
  スロットインデックスのみ）のため、注入との厳密な個体対応付けはできない。代わりに
  `eval/scoring.score_concept_discovery()` が、候補クラスタのメンバー観測時刻と注入
  イベント時刻が時間的に近接するか（既定5秒以内）で「命中」を判定する時間近接性の
  代理指標を使う（`injected_concept_top5_hit`：3種中何種が命中したか）。
- 「候補の説明の妥当性（評価者3名の一致率）」は人手評価が必須のため、本実験は測定しない。
  EXP-10 と同様、Claude Code は候補生成までの基盤を用意し、最終判定は人が行う
  （`docs/results/EXP-08.md` に候補の一覧を出力するので、それを見て評価者が判定する）。

## 合格基準

`experiments/criteria.yaml` の `H7` を参照（本ファイルには転記しない、唯一の置き場）。

## smoke と本実行

- smoke（`SMOKE=1`）：seed 1本、`p2_concept`（2エピソード×30秒）、mock LLM。
- 本実行：`config.yaml` の `full_run` を参照。実LLMプロバイダへの切替と、poc_plan.md
  6.2 通りの運用期間相当のデータ量が必要。加えて、評価者3名による候補判定（人手、
  `docs/results/EXP-08_protocol.md` を用意して実施）が必要。
