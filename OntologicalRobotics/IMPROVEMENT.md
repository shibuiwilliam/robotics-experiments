# IMPROVEMENT.md — ORX レビューと修正計画

レビュー日: 2026-06-13 / 対象: `OntologicalRobotics/`（ORX）コードベース＋ドキュメント全体
基準文書: `PROJECT.md`（H1–H7・§5.2不変条件・§7評価方法論・§9フェーズ・DoD）、`CLAUDE.md`、
`SCENARIOS.md`（T8–T14・§2.2反証・§3.3成果物・§4受入・§5順序/M-Scenario-A・§6報告）、各ミッションのDoD。
検証方法: 全テスト実行（187 passed・lint clean・import契約 green）＋該当ファイルの精読・grep実証。

---

## 0. 総評（結論）

**基盤は本物。捏造・リギング（仕込み）・真値漏洩は検出されない。** しかし**現状はプロジェクトの
中核目的（仮説 H1–H7 の検証）を満たしていない**。理由は二つ:

1. **計測しているものが仮説本体より狭い（scope inflation by adjacency）。** 「F1=1.000」「p<0.01」等の
   見出し数値は、(a) 決定的リファレンスソルバ（期待SPARQL/SQL）の**表現上限**、(b) 機構アブレーション
   （anchoring on/off）、(c) 較正学習曲線、のいずれかであり、**仮説の本体である「オントロジーを使う
   LLMエージェントが生データのエージェントに勝つ」というエージェントレベルの比較は一度も実行されて
   いない**（すべて live モード待ち）。H4/H7 に至っては stub（偽）埋め込み・0トークンの上で数字が出ている。
2. **シナリオ・プログラム（T8–T14）が完全には実装されていない。** 全7シナリオ中**実装済みは S1 のみ**。
   S2 はオラクル＋スキーマのみ（≈15%）、S3–S7 は設計文書のみで未着手 → マイルストーン **M-Scenario-A 未達**。
   **要件: S1–S7 を全件ゲート閉まで実装する（C3）。**

つまり「**真理グラフ評価・反実仮想リプレイ・アンカリング機構**という装置は確かに動く（＝シミュレーション
研究の貢献の核は本物）」が、「**それらを使って H1–H7 を反証可能に検証した**」とまでは言えない。
ドキュメントは live ギャップを随所で正直に注記しているものの、要約表の体裁（✅／1.000／微小p を同一セルに
併記）が「仮説確認済み」と誤読されやすい。**最優先の修正は、計測の射程を表記で厳密に分離すること**である。

### 本物だと確認できたもの（公平のため明記）
- 真値と被験系の分離: `oracle_truth_ids`/`TruthState` を anchoring/kg/agent が読まないことを AST テストで
  強制（`tests/test_architecture.py:83-126`）。採点は独立。**真値漏洩なし。**
- S1/T1 の反証は構造的に正当: B0/B1 ソルバはシグネチャで情報境界を強制（`s1_lot_recall/reference.py`）、
  世界設計が同一性を要求するための正当な設計。理想推論器でも B1 条件では搬送済み個体を解けない。
- アンカリング D1（静止×搬送＝等速予測、ADR-010）、信念調停（確信度×新しさ）、忠実度メトリクスは
  実装が本物で機構レベルでは意味のある結果（T1 p=3.8e-06、T4 乖離領域、T3/T6 Brier 0.030→0.006）。

---

## 1. CRITICAL — 中核目的（仮説検証）の妥当性

### C1. H1/H4/H6/H7 はエージェントレベルで未検証。表記が「仮説確認」と誤読される
- **根拠**: `configs/experiments/t2_business.yaml:16`・`t7_sop.yaml:12`・`t5_onboarding.yaml` が
  `mode: stub`。stub LLM は `stub-response:<hash>` を返す（`common/providers.py:121-123`）ため、
  T2 のエージェント3条件(OR-full/B1/B0)は全て 0.000、報告される 1.000 は `OR-reference`＝
  人手の期待SPARQL/SQL（`exp/suites/t2.py:209-268`）。`docs/PROGRESS.md:152-159` の要約表は
  P2(H6,H7)・P5(H1) を「✅構築/⏳live」とし同セルに 1.000 を併記。
- **なぜ目的に反するか**: PROJECT.md の H1–H7 は「エージェントの計画品質・正答率・トークン効率」を問う。
  表現上限（決定的ソルバ）は「オントロジーが**表現できる**」ことの証明に過ぎず、「**エージェントが使って
  勝つ**」という仮説本体ではない。H7（トークン効率）は 0 トークンで**全く測られていない**。
- **修正案**:
  1. PROGRESS.md と各レポートのゲート表記を **「表現上限（決定的・LLM不要）」/「機構アブレーション」/
     「エージェント検証（live・未実行）」** の3カテゴリに明示分離する。「✅」は機構/表現のみに付け、
     仮説のエージェント検証は別列で「未実行」と明記。
  2. README の「実装状況」に「**H1–H7 のエージェントレベル検証は未実行（live計測）**」を最上段に1行で明記。
  3. live 計測の実行（OPENAI_API_KEY＋コスト承認、~0.9–1.5Mトークン）。これが本来の目的達成の本丸。

### C2. 科学的に無意味な p 値の提示（決定的ソルバ vs ランダムノイズ）
- **根拠**: T7 は stub 埋め込みが既定（`configs/experiments/t7_sop.yaml:12`、`common/providers.py:186-205`
  は sha256 由来の無意味な単位ベクトル）。`reports/exp-t7-sop-77747c4a.md` の onto-guided P@1=1.000 vs
  vector-rag 0.028、**McNemar p=1.69e-21** は「決定的グラフ走査 vs ランダムベクトルの top-1」の比較で、
  H4（双対表現）の証拠にはならない。
- **修正案**: stub モードの vector-rag 行・p 値はレポートから「ハーネス検証（数値は無意味）」と明示するか
  非表示にし、**H4 の主張に p 値を引用しない**。本計測は OpenAI 埋め込み（live）でのみ行う。

### C3. 全シナリオ（S1–S7 / T8–T14）を完全実装する（現状 S1 のみ・他6件 未完）
- **要件**: `SCENARIOS.md`（T8–T14）と `docs/scenarios/` の7シナリオを**全件**、各々ゲート閉まで
  完全実装する（Tier A だけでなく Tier B・Tier C も含む）。文書だけのシナリオは「未実装」とみなす。
- **根拠（実体・2026-06-13 確認）**: suite code は `src/orx/exp/suites/s1_lot_recall/` のみ、
  world/exp config・CLI登録（`orx scenario list`）も `s1` のみ。oracle は s1/s2、テストは
  tests/scenarios/{s1,s2}（S2 はオラクル単体7件のみ）。S3–S7 は `docs/scenarios/*.md` の設計のみ。
- **なぜ目的に反するか**: PROJECT.md §8.2 / §12 と SCENARIOS.md は H1–H7 を業務文脈で実証するために
  T8–T14 を要求する。S1 だけでは Tier A すら未完（M-Scenario-A 未達）であり、業務検証の論証が欠ける。
- **各シナリオの実装状況と必要作業**（各々 §3.3 全成果物＝suite code・world/exp config・語彙＋SHACL＋CQ・
  業務データ・oracle 真値導出・反証テスト・replay同一性・メタモルフィック・demo・登録・ゲート）:

  | S | Suite | 状況 | 主な未実装 / 固有作業 |
  |---|-------|------|----------------------|
  | S1 | T8 | ✅ 完了 | （ゲート閉） |
  | S2 | T9 | ✅ 完了 | （ゲート閉。OR-full 汚染F1=1.0/違反0、4条件の構造的失敗を実証） |
  | S3 | T10 | ❌ 未着手 | 多ベンダー異種・ProcessRequirement 突合せ・故障の経年劣化・T3/T5/T6 の業務文脈化 |
  | S4 | T11 | ❌ 未着手 | X4 ID無し台帳アンカリング・2視点・矛盾観測の信念調停・SOP起票連鎖 |
  | S5 | T12 | ❌ 未着手 | **新規 `ontology/domains/normative.ttl`**（deontic）・X2/X3/X5・custody連鎖・監査可能性指標 |
  | S6 | T13 | ❌ 未着手 | **OR-sym / OR-vec 条件を `exp/episode.py` CONDITIONS に追加**・双対表現経路・X4・X6 非対称コスト |
  | S7 | T14 | ❌ 未着手 | X4・X5・所有(情報的関係)＋最終目撃＋視覚署名の三系統融合・誤配送採点 |

- **実装順序（SCENARIOS.md §5 厳守）**: **Tier A**: S1✅ → S2 → S6 → **M-Scenario-A**（S1+S2+S6×全条件の
  比較レポート）。**Tier B**: S3 → S7 → S4。**Tier C**: S5（規範層 normative.ttl・Tier A/B ゲート閉後）。
- **横断インフラ**（共有・再利用・コピペ禁止）: X2 接触蒸留（S2/S5）, X3 状態リセット（S2/S5）,
  X4 ID無し同一性（S4/S6/S7）, X5 人間アクタ（S5/S7）, X6 非対称コスト（S6）。X1 は実装済（S1）。
- **完了定義**: 全7シナリオで `make scenario-validate`（demo の反証 green ＋ tests/scenarios 緑）が通り、
  `orx scenario list` に S1–S7 が implemented で並び、M-Scenario-A 比較レポートが生成されること。
- **射程の注記（C1 と整合）**: 各シナリオの数値も ceiling/ablation/agent を分離報告し、決定的経路の
  数値を仮説確認として提示しない（agent 検証は live・別件）。

---

## 2. HIGH — DoD・再現性・評価方法論の穴

### H-1. 新語彙にクラス固有 SHACL shape が無い
- **根拠**: `ontology/shapes/` は `claims.ttl` のみ。S1 語彙（`orx-biz:Lot/memberOfLot/RecallOrder`、
  `orx-st:QuarantineZone`）・S2 語彙（`ContactEvent` 等）を参照する shape は皆無（grep 実証）。
- **ニュアンス**: 汎用 `ClaimShape`（`claims.ttl:11-12`, targetClass `orx-prov:Claim`）が全クレームの
  来歴・確信度・時刻は担保するため「全書込パスは SHACL 検証下」という不変条件は維持されている。
  欠けているのは**クラス固有の構造制約**（例: `RecallOrder` は `targetsLot` をちょうど1つ、`memberOfLot`
  の range は `Lot`）。
- **なぜ目的に反するか**: ミッション DoD「vocabulary additions ... with **matching SHACL shapes** and
  new competency questions」、SCENARIOS.md §6。CQ は追加済みだが shape が未充足。
- **修正案**: `ontology/shapes/s1_lot_recall.ttl`（および S2 用）を追加し、新クラスの構造制約を定義。
  `tests/scenarios/s{n}/` で適合/不適合フィクスチャを検証。

### H-2. シナリオ出力に git commit / モデルスナップショットのスタンプが無い
- **根拠**: `S1Result`（`s1_lot_recall/runner.py:55-66`）は `config_hash`・`seeds` を持つが
  `git_commit`・`llm_model`（モデルスナップショット）が無い。per-episode の `manifest.json` には
  両方ある（`replay/io.py` RunManifest）が、シナリオ集約 `results.json` には伝播していない。
- **なぜ目的に反するか**: DoD「Reproducibility stamps (config hash, seed, **git commit, model snapshot**)
  appear on **all** scenario outputs」。
- **修正案**: `ScenarioExperimentConfig` 実行時に git commit とプロバイダのモデル名を解決し、全シナリオ
  result モデル（S1Result 等）と results.json に焼き込む。共通ヘルパ（既存 `episode._git_commit`）を再利用。

### H-3. シナリオ run の replay-identity テストが無い
- **根拠**: 基盤は `tests/exp/test_episode.py::test_replay_identity_byte_identical` で担保。
  `tests/scenarios/` には replay 系テストが皆無（grep 実証）。
- **なぜ目的に反するか**: DoD「replay-identity holds for scenario runs」。決定的ソルバなので実態は
  再現するはずだが、**テストで保証していない**＝退行検知不能。
- **修正案**: `tests/scenarios/s1/` に「同一 run を2回評価して results が一致」する replay-identity
  テストを追加（メトリクスの正準JSONバイト一致、ADR-008方式）。

### H-4. メタモルフィック・テスト（PROJECT.md §7.4 / 評価4本柱の1つ）が皆無
- **根拠**: `src/`・`tests/` に metamorphic 関連実装ゼロ（grep 実証）。PROGRESS は「live計測と同時」
  としているが、**構造クエリ（ロット番号・バーコードのリネーム、ゾーン同義語置換）に対する
  リファレンスソルバ/CQ の回答不変性はオフラインで検証可能**であり、live を待つ必要はない。
- **なぜ目的に反するか**: §7.4 は「オントロジー構造への依拠」と「文字列表層一致による見かけの成功」を
  切り分ける装置。これが無いと「グラフが本当に構造で解いているか」を主張しきれない。
- **修正案**: 少なくとも決定的経路（OR-reference/OR-full ソルバ、CQ）に対しメタモルフィック変換
  （識別子リネーム）下で回答集合が不変であることのテストを `tests/` に追加。LLM 層のメタモルフィックは
  live で別途。

---

## 3. MEDIUM — 整合性・運用・解釈

### M-1. ドキュメントの情報源が3系統で陳腐化リスク
- **根拠**: `docs/scenarios/*.md`（前回の散文・"提案"T8–T14）／`SCENARIOS.md` v1.0（authoritative）／
  `docs/SCENARIO_IMPLEMENTATION_PLAN.md`。番号体系は一致し矛盾はないが、散文版は spec 確定前の提案で重複。
- **修正案**: `docs/scenarios/README.md` 冒頭に「**authoritative は SCENARIOS.md。本ディレクトリは
  着想メモ（superseded）**」と明記、または `docs/archive/` へ移動。

### M-2. `tasks/suites/` が T1–T7 については空（PROJECT.md §11 との不一致）
- **根拠**: PROJECT.md §11 は `tasks/suites/` を「T1〜T7 定義」の場所とするが、実装は
  `src/orx/exp/suites/`。`tasks/suites/` は S1 の宣言 spec のみ。ADR-013 はシナリオの方針は述べるが
  T1–T7 の齟齬は未解消。
- **修正案**: PROJECT.md は改変禁止のため、(a) ユーザーに §11 解釈の追認を求める、または
  (b) T1–T7 にも `tasks/suites/t{n}_*/README.md` の宣言 spec を置き一貫させる。decisions.md に記録。

### M-3. ゲートの 1.000 は「ノイズ0飽和」かつ「ハードケース n=1/シード」で過大に強く見える
- **根拠**: 例 S1 は回収対象3個体中ハード（搬送済みID不可読）は b3 の1件のみ。OR-full=1.000 は
  「唯一のハードケースが解けた」を意味し、ハード事例数が少ない。Agent監査の Finding 7。
- **修正案**: ハードケースを複数（複数搬送・複数ロット同時回収・部分的ID失敗）に増やすか、
  受入の文言に「ハード事例数」を明記。頑健性掃引（既に良い）を主指標として前面に出す。

### M-4. 生成物の untracked 放置・残骸
- **根拠**: `OntologicalRobotics/reports/scenario-s1-89389d38.md` が untracked、
  `ontology/mappings/proposals/vendor_fuzz_000.yaml` は onboard デモの残骸。
- **修正案**: `reports/` 配下の生成 md と `ontology/mappings/proposals/` を `.gitignore` 対象にするか、
  代表レポートのみ意図的にコミット。残骸 yaml は削除。

---

## 4. LOW

- **L-1**: 中身のあるディレクトリに空 `.gitkeep` が残存（`ontology/domains/.gitkeep` 等）。削除可。
- **L-2**: S2 語彙（ContactEvent/CleaningEvent/possiblyContaminatedBy）に CQ 未追加（S2 実装時に
  §6 に従い追加すること。現状はオラクルのみで vocab 未確定のため許容）。

---

## 5. 修正の優先順位（推奨実行順）

1. **C1（表記の分離）** — 最優先・低コスト・高効果。PROGRESS/README/レポートの体裁を直し、
   「表現上限／機構／エージェント検証」を分離。これだけで「仕込みに見える」リスクが大きく減る。
2. **C2** — T7 の無意味 p 値を是正（H4 主張に引用しない）。低コスト。
3. **H-2 / H-3 / H-1** — シナリオ DoD の穴（スタンプ・replay テスト・SHACL shape）。S2 着手と同時に
   横断インフラとして一度に整備すれば S2/S6 にも効く。
4. **C3** — S2 完成 → S6（OR-sym/OR-vec 追加）→ M-Scenario-A。本体の前進。
5. **H-4（メタモルフィック）** — 決定的経路だけでも先行実装（live 不要）。
6. **M-1〜M-4, L-1, L-2** — 整理・運用。随時。
7. **（別件・本丸）** live 計測の実行で H1–H7 をエージェントレベルで初めて検証。コスト承認が前提。

---

## 6. 一行サマリ

> ORX の**装置（sim・perception・anchoring・kg・oracle・replay・exp・シナリオ基盤）は本物で、
> リギングも真値漏洩も無い**。だが**仮説 H1–H7 はまだ「表現できる／機構が効く」までしか示せておらず、
> 「エージェントが使って勝つ」という本体は未計測**。さらに**シナリオ Tier A は S1 のみ完了**。
> まず**計測射程の表記分離**で過大主張リスクを断ち、次に **S2→S6→M-Scenario-A** と **live 計測**で
> 目的を実体的に満たす。

---

## 7. 是正状況（2026-06-13・docs/REMEDIATION_PLAN.md 駆動）

| 項目 | 状態 | 対応 |
|------|------|------|
| C1 表記の射程分離 | ✅ 解決 | `src/orx/exp/scope.py` 新設。T2/T5/T7/S1 レポート＋PROGRESS全体表＋README に ceiling/ablation/agent(未実行) を明示分離 |
| C2 無意味p値 | ✅ 解決 | T7(他) の stub p値を「参考・無意味」表記化、H4 主張から除外。`scope.stub_warning` |
| H-1 クラス固有SHACL | ✅ 解決(S1) | `ontology/shapes/scenarios.ttl`（RecallOrder/memberOfLot）＋ `tests/scenarios/s1/test_shapes_s1.py`。S2/S6 は各実装時に追加 |
| H-2 再現性スタンプ | ✅ 解決 | S1Result に git_commit/orx_version/model_snapshot。results.json に焼込（test_replay_identity_s1） |
| H-3 シナリオreplay-identity | ✅ 解決 | `tests/scenarios/s1/test_replay_identity_s1.py`（正準JSONバイト一致） |
| H-4 メタモルフィック | ✅ 解決(決定的経路) | `src/orx/exp/metamorphic.py`＋`tests/scenarios/s1/test_metamorphic_s1.py`（識別子リネーム不変）。LLM層は live |
| M-1 docs/scenarios 陳腐化 | ✅ 解決 | README 冒頭に SUPERSEDED 明記（authoritative=SCENARIOS.md） |
| M-2 tasks/suites 整合 | ✅ 解決 | ADR-016（宣言spec方針）。PROJECT.md は改変せず解釈を記録 |
| M-4 生成物の残骸 | ✅ 解決 | reports/*.md と mappings/proposals/ を .gitignore、追跡解除、fuzz残骸削除 |
| L-1 空.gitkeep | ✅ 解決 | 中身のあるディレクトリの .gitkeep 削除 |
| C3 **全シナリオ S1–S7 完全実装** | ⏳ 進行中（**2/7**: S1,S2✅） | 残 S6→M-A→S3→S7→S4→S5。完了定義=`make scenario-validate` 全件緑＋`orx scenario list` 全件 implemented＋M-Scenario-A レポート |
| M-3 ハードケース n=1 | ⏳ 各シナリオで対応 | 受入文言に射程明記済。各シナリオ世界で複数ハードケース化 |
| L-2 シナリオCQ（S2–S7） | ⏳ 各シナリオで対応 | 各シナリオ実装時に語彙＋CQ＋SHACL をセットで追加（SCENARIOS.md §6） |
| 本丸: live計測 | ⏳ 別件 | OPENAI_API_KEY＋コスト承認が前提。H1–H7 のagent検証はこれで初実施 |
