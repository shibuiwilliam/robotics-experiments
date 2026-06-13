# ORX 進捗トラッカー

> `docs/IMPLEMENTATION_PLAN.md` のチェックリスト鏡像。セッション開始時はここを読み、最初の未チェック項目から再開する。
> 各フェーズゲートでは完了基準の**測定値**をこのファイルに記録する。

## Scaffold（計画承認前の足場 — 完了）

- [x] リポジトリ構成（PROJECT.md §11 準拠）、pyproject.toml、uv環境
- [x] ツーリング: ruff（T20=print禁止）、pytest、import-linter（依存方向＋oracle分離契約）
- [x] 空テストスイート緑（14 passed: import・CLI・アーキテクチャ契約）
- [x] docs/IMPLEMENTATION_PLAN.md 作成
- [x] **計画のユーザー承認**（2026-06-12、D1–D4の推奨案込み）

## Phase P0 — 基盤 ✅（2026-06-12 完了）

- [x] P0-01 common.schemas（pydanticメッセージ）
- [x] P0-02 common.iri
- [x] P0-03 common.seeding
- [x] P0-04 common.logging（structlog JSONL）
- [x] P0-05 common.providers（openai/cache/stub）
- [x] P0-06 common.config（YAML＋構成ハッシュ）
- [x] P0-10 C1 ミニ倉庫世界 v0（1アーム・8箱・オフスクリーン224px・真値アクセサ）
- [x] P0-11 C1 ベンダースキーマA＋劣化ノブ骨格（全ノブ0、delay/contradictionはP4で実装）
- [x] P0-12 C4 合成検出器＋CLIP埋め込み（MPS/CPU/stub）
- [x] P0-13 C4 知覚パスプロファイル → docs/design/perf_p0.md（全予算クリア）
- [x] P0-14 オントロジー v0（upper/spacetime/agency TTL＋SHACL shapes＋sameAs禁止shape）
- [x] P0-15 C6 kg（書込API・named graphクレーム・調停・物質化・LanceDB索引）
- [x] P0-16 C5 アンカリング骨格（ID決定的＋最近傍ゲート、OR−identityアブレーション切替）
- [x] P0-17 C8 oracle（真値ABox＋忠実度メトリクス、ORコア不依存）
- [x] P0-18 C9 replay（記録・マニフェスト・反実仮想リプレイ）
- [x] P0-19 CQフレームワーク＋CQ 4問（tasks/competency_questions/）
- [x] P0-20 CLI（demo / sim run / replay / report / cq / version）
- [x] P0-21 **フェーズゲート** — 測定値（demo_tiny 12s, seed 7, ノイズ0）:
  - 忠実度: トリプルF1=**1.000**（基準>0.95 ✓）、同一性F1=1.000、位置RMSE=0.000m、
    遷移遅延=0.0s、取りこぼし=0、陳腐化率=0
  - リプレイ同一性: metrics.json **バイト一致** ✓（tests/exp/test_episode.py）
  - 反実仮想: OR-no-identity リプレイで同一性F1が劣化（条件切替が機能）
  - 性能: 60sエピソード記録1.06s（予算120s）、リプレイ0.12s（予算30s）
  - テスト: 108 passed（単体・CQ回帰・SHACL・リプレイ同一性・アーキテクチャ不変条件）
  - 補足: D2のon/containsはP0世界に積み重ねが無いため未使用（語彙は定義済み、P1世界で有効化）

## Phase P1 — アンカリング・同一性（T1）✅（2026-06-12 完了）

- [x] P1-01 C1 モバイルベース（擬似LiDAR・cm単位・ID不可読のベンダースキーマB）＋slide搬送
- [x] P1-02 C2 skills骨格 → **P3へ延期**（ADR-009: T1の被験変数は決定の正しさであり、
      物理ピックはスキル導入(P3)で接続。物理再実行なしの反実仮想リプレイと整合）
- [x] P1-03 C5 本格アンカリング（D1最終形: 静止仮説＝密度正規化ガウス ×
      搬送仮説＝等速予測ガウス、埋め込み乗法変調、イベント内大域貪欲割当、
      識別子衝突分裂。ADR-010）
- [x] P1-04 C10 expランナー（条件×シード、記録1回→全条件リプレイ、McNemar/
      Wilcoxon/ブートストラップCI、`orx exp run`・exp対応 `orx report`）
- [x] P1-05 条件定義（OR-full / OR-no-identity、コンフィグのみで切替）
- [x] P1-06 T1スイート（シード駆動生成・決定的実行器・真値採点）
- [x] P1-07 **フェーズゲート** — 測定値（t1_handoff、20シード、劣化: ID読取失敗5%・
      姿勢ノイズ2cm・オクルージョン2%）:
  - T1成功率: **OR-full 0.950 vs OR−identity 0.000**（不一致 19/0）
  - **McNemar p = 3.81e-06** ✓ 有意、Wilcoxon(同一性F1) p < 0.001
  - 同一性F1平均: OR-full 0.985 vs OR−identity 0.000
  - 成果物: data/runs/exp-t1-identity-275e01f8 / reports/exp-t1-identity-275e01f8.md
  - テスト: 120 passed（T1縮小版統合テスト含む、完全オフライン）

## Phase P2 — 業務ブリッジ・エージェント（T2, T7）✅ 構築完了 / ⏳ live計測待ち

- [x] P2-01 C3 模擬WMS（SQLite, 受注/SKU/出荷指示）＋SOPコーパス＋識別子スレッド
      （受注→出荷指示→バーコード→物理個体、シード決定的）
- [x] P2-02 business.ttl＋WMS/SOPの主張化（来歴=wms/sop-registry、確信度1.0）
- [x] P2-03 アイデンティティ・スレッド表現（バーコード同値結合）。D2述語の拡張は
      せず **T2リファレンスソルバ＝期待SPARQL回帰**で業務表現を担保（理由:
      WMS由来主張の忠実度は自明に1.0で感度がない。decisions.md ADR-011）
- [x] P2-04 C7 agent（ツール使用ループ、SPARQL/業務SQL/生観測ツール、
      トークン・ツール呼出の常時記録 = H7）
- [x] P2-05 ベースライン B0（生データ文脈投入）/ B1（個別スキーマツールのみ）
- [x] P2-06 T2スイート（6タイプ54問×3シード世界、真値照合採点、負例含む）
- [x] P2-07 T7スイート（onto-guided vs vector-rag、SOP 18文書/世界、72クエリ）
- [ ] P2-08 メタモルフィック変換 v1 → **live計測と同時に実施**（表層パラフレーズの
      回答不変性はLLM挙動の検査であり、stubでは無意味）
- [x] P2-09 **フェーズゲート（オフライン分）** — 測定値:
  - **T2 表現上限（OR-reference = 期待SPARQL/SQL）: 正答率 1.000**（54/54問、
    3シード、搬送済み箱の越境同一性ブリッジ込み）→ H6の表現側は成立
  - **T7 onto-guided: P@1 = 1.000**（72クエリ）→ オントロジー誘導検索の上限
  - エージェント3条件（OR-full/B1/B0）はstubでハーネス完走を確認（正答率は無意味）
  - 成果物: data/runs/exp-t2-business-5989b2fc / exp-t7-sop-77747c4a＋各レポート
  - テスト: 135 passed
  - ⏳ **残: LLM live計測**（T2 3条件×54問、T7 vector-rag、メタモルフィック）。
    概算 ~0.9〜1.5Mトークン → CLAUDE.md §6 によりユーザー承認＋OPENAI_API_KEY が必要。
    実行手順: configs/experiments/*.yaml の provider.mode を openai に変更して
    `orx exp run`（全応答キャッシュ→以後 mode=cache で再現）

## Phase P3 — 能力契約・故障注入（T3, T6）✅（2026-06-12 完了）

- [x] P3-01 C2 スキルサーバ＋故障注入器（重量超過・素材適性・リーチ、シード決定的。
      真値プロファイルはC2のみが解釈 — 計画側は台帳推定のみ）
- [x] P3-02 capability.ttl＋能力契約（宣言: スキル/payload/reach/事前成功率。
      arm_heavy は反射素材の弱点を**宣言しない**過信設計 = 較正の検証対象）
- [x] P3-03 能力台帳の経験更新（orx-cap:trials/successes を関数的主張として
      世界グラフへ、ベイズ事後平均で推定）
- [x] P3-04 計画の能力クエリ統合（実現可能性フィルタ＋台帳argmax＋ε探索＋
      失敗時の次善再計画。決定的計画器 — LLM計画はlive拡張)
- [x] P3-05 T3スイート（割当正答率, 対round-robin）/ T6スイート（較正曲線）
- [x] P3-06 **フェーズゲート** — 測定値（5シード×30エピソード×8タスク）:
  - **Brier（信頼性項）: 序盤5ep 0.030 → 終盤5ep 0.006**（較正が改善 ✓）
  - 較正MAE: 0.116 → 0.055（宣言の過信が経験で修正される）
  - T3割当正答率: **capability 0.968 vs round-robin 0.625**、McNemar p=6.1e-102
  - 再計画 101回（失敗→次善ロボット）
  - 注: 生Brierは方策改善に伴う結果分散を含み単調でないため、ゲートは
    Brier分解の信頼性項で判定（レポートに両方記載）
  - 成果物: data/runs/exp-t3-capability-30930cff / レポート
  - テスト: 144 passed

## Phase P4 — 信念管理・劣化掃引（T4）✅（2026-06-12 完了）

- [x] P4-01 劣化ノブ完備（sim/perception側のみ）: ID読取失敗・姿勢ノイズ・
      オクルージョン・**観測遅延**（パイプライン遅延キュー、LiDAR系ロボット対象）・
      **矛盾観測**（ステイルなトラックキャッシュの再送出 — 同一個体への矛盾主張を
      生む設計。位置のみの破損はアンカリングが別個体に逸らして調停が無効になる
      ことを実測し再設計した）
- [x] P4-02 信念調停本格化: 「確信度 × 新しさ（指数減衰）」スコアで関数的述語の
      矛盾解消＋TTL失効。OR−belief = メタデータ意味論オフ（失効なし・到着順）
- [x] P4-03 T4掃引ランナー（記録1回→2条件リプレイ、ノブ値毎の対応ありブート
      ストラップCI、乖離領域の自動検出）＋頑健性曲線レポート
- [x] P4-04 **フェーズゲート** — 測定値（contradiction_rate掃引、5シード）:
  - トリプルF1曲線: OR-full 0.996/0.980/0.974/0.969/0.967 vs
    OR−belief 0.996/0.970/0.969/0.966/0.964（ノブ 0/0.1/0.2/0.3/0.45）
  - **乖離領域: contradiction_rate ∈ {0.1, 0.2, 0.3, 0.45}**（差のCI95が0を上回る）
  - ノブ0では両条件一致（H5の効果は劣化領域でのみ立ち上がる — 予測どおり）
  - 成果物: data/runs/exp-t4-contradiction-4beb183f-3 / レポート
  - テスト: 149 passed

## Phase P5 — スキーマ・ファジング・オンボーディング（T5）✅ 構築完了

- [x] P5-01 スキーマ・ファジング生成器（コンテナ名/ネスト構造/フィールド名/
      単位(m,cm,mm)/識別子有無 を確率的に変異、シード決定的、真マッピング付き）
- [x] P5-02 `orx onboard <vendor-schema>` ワークフロー: マッピング案生成
      （heuristic=規則ベース / llm=LLM支援・live）→ サンプル適用検証
      （リフティング成立＋座標妥当性）→ レビュー用提案ファイル出力
- [x] P5-03 T5スイート＋統合コスト比較レポート
- [ ] P5-04 メタモルフィック最終版＋H7横断分析 → **live計測と同時**（表層
      パラフレーズ不変性・正答率/トークン集計はLLM挙動の測定）
- [ ] P5-05 （任意）小型実VLA差し替え — ユーザーの明示要求があれば実施
- [x] P5-06 **フェーズゲート** — 統合コスト比較（20合成スキーマ×5シード）:
  - **heuristic支援: フィールド精度 1.000 / 人手修正 0.0行 / 検証合格率 1.00**
    vs 手書きベースライン 10.8行 → ハブ&スポーク統合コストの大幅圧縮（H1）
  - 注: ファズ空間が規則ベースの射程に収まる設計のため heuristic が飽和。
    語彙逸脱の大きい自然スキーマ＋LLM支援の比較が live 拡張（同一ハーネスで
    provider.mode=openai のみで実行可能）
  - 単位推定の境界バグ（1500mm→cm誤判定）をonboardデモで検出し修正済み
  - 成果物: data/runs/exp-t5-onboarding-9591cf5a / レポート
  - テスト: 158 passed

## 全体状況（2026-06-12 時点）

| Phase | 状態 | ゲート測定 |
|-------|------|-----------|
| P0 基盤 | ✅ | 忠実度F1=1.000（>0.95）、リプレイ同一性=バイト一致 |
| P1 同一性 (H2) | ✅ | T1: 0.950 vs 0.000、McNemar p=3.8e-06 |
| P2 業務+エージェント (H6,H7) | ✅構築/⏳live | T2表現上限1.000・T7 onto 1.000。LLM 3条件はlive待ち |
| P3 能力契約 (H3) | ✅ | Brier信頼性 0.030→0.006、割当 0.968 vs 0.625 |
| P4 信念管理 (H5) | ✅ | 乖離領域 contradiction ∈ {0.1..0.45} |
| P5 オンボーディング (H1) | ✅構築/⏳live | heuristic 1.000/0.0修正行。LLM支援はlive待ち |

**live計測（要 OPENAI_API_KEY＋コスト承認）**: T2エージェント3条件（54問）、
T7 vector-rag、T5 llm支援、メタモルフィック、H7知識効率。概算 0.9〜1.5Mトークン。
手順: 各 configs/experiments/*.yaml の provider.mode を openai にして
`uv run orx exp run <config>`（全応答キャッシュ→以後 mode=cache で再現・無料）。

## シナリオ・プログラム（T8〜T14、SCENARIOS.md v1.0）

計画: `docs/SCENARIO_IMPLEMENTATION_PLAN.md`（承認済み 2026-06-13）。決定 D1/D2/spec-commit 解決済み
（ADR-013/014）。前提監査 PASS（P0〜P5 完了・158 tests green）。

### 共通インフラ X1〜X6（各シナリオと同時に実装）
- [x] X1 割込み指示イベント（S1と同時）— 回収割込みを recall_time での意思決定として実装
      （割込み＝記録された時刻での回収クエリ＋再評価。決定レベル採点 ADR-009 と整合）
- [~] X2 接触イベント蒸留（S2と同時）— ContactEvent スキーマ済。蒸留(sim接触)は実装中
- [x] X3 状態リセットイベント（ResetEvent スキーマ＝洗浄/施錠/認証）
- [ ] X4 ID無し同一性解決（S6と同時）
- [ ] X5 人間アクタ（S5/S7時）
- [ ] X6 非対称コスト採点（S6と同時）

### CLI・レポート拡張
- [x] `orx scenario list` / `scenario demo s{n}` / `scenario run <cfg>`
- [x] `orx report` シナリオテンプレート（失敗予言検証・頑健性曲線・トークン効率）

### Tier A → M-Scenario-A
- [x] S1 ロット回収（T8 / X1 / H2,H6,H7）✅（2026-06-13）— 測定値（8シード, t8 world）:
  - **受入: OR-full 列挙F1=1.000・完遂率=1.000**（ノイズ0）✓
  - 反証 green: B0_misses_in_transit ✓（B0 completion 0.646, membership_recall<1）/
    B1_stale_location ✓（B1 membership_F1=1.0 だが location 0.646）/ OR_full_succeeds ✓
  - McNemar OR-full vs B0/B1: **p = 7.81e-03**（8/8 discordant）
  - 頑健性: id_read_failure 0→0.6 で OR-full=1.0 維持、B0 0.65→0.31 劣化（H2/H6の堅牢性）
  - 成果物: data/runs/scenario-s1-89389d38 / reports/scenario-s1-89389d38.md
  - テスト: 175 passed（oracle単体4・反証6・CQ3・統合5 を追加）
- [~] S2 アレルゲン（T9 / X2,X3 / H5）**実装中** — 汚染推移閉包オラクル(ADR-015)＋
      X2/X3スキーマ＋oracle単体7件 green。残: 世界の接触dynamics・条件別ソルバ・反証・掃引・gate
- [ ] S6 リサイクル（T13 / X4,X6 / H4）— 受入: コスト加重で OR-full > 両アブレ・較正曲線
- [ ] M-Scenario-A: 3シナリオ×全条件の比較レポート提示

### Tier B（Tier Aゲート後）
- [ ] S3 製造ライン（T10 / H1,H3） / [ ] S7 介護（T14 / H2,H4,H6） / [ ] S4 プラント（T11 / H2,H5）

### Tier C（Tier Bゲート後・規範層新設）
- [ ] S5 病院（T12 / 規範層 / normative.ttl）
