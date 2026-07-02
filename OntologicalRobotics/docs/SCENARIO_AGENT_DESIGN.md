# SCENARIO_AGENT_DESIGN.md — シナリオに agent（live）射程を足す設計（R-3a）

作成日: 2026-06-15 / 基準: `IMPROVEMENT.md` R-3a, `PROJECT.md`（§5.2-5 不変条件・§8.1 条件・§12）, `CLAUDE.md` §6。
状態: **設計確定・課金実行は承認待ち（ゲート §5）**。

---

## 1. 問題

S1–S7 のシナリオ数値はすべて**決定的リファレンスソルバ**（ceiling/ablation 射程）。
「業務シナリオで **LLM エージェント**がオントロジーを使って生データ/個別スキーマの
エージェントに勝つ」という agent 射程は未計測。agent 実証は今のところ抽象タスク
T2/T5/T7 の live に限られる（`docs/LIVE_RESULTS.md`）。R-3a はこれをシナリオへ広げる。

## 2. 設計（T2 の live 経路を再利用）

抽象タスク T2 は既に **OR-full / B1 / B0 の LLM エージェント条件**を実装済み
（`src/orx/agent/toolloop.py` の `ToolAgent` ＋ `t2.answer_questions`、条件別ツール）。
シナリオ agent 条件はこれを**そのまま流用**する。新規シナリオごとに必要なのは:

1. **タスクのクエリ化**: シナリオの決定的タスクを自然言語の質問＋真値に変換する
   （例 S1: 「回収ロット L の対象個装バーコードと現在ゾーンを列挙せよ」＋ oracle 真値）。
2. **条件別ツール束**（T2 と同形）:
   - OR-full: `sparql_query`（世界グラフ）＋ `business_sql`（lot 台帳）。
   - B1: `robot_observations`（個別スキーマ生観測・横断融合なし）＋ `business_sql`。
   - B0: ツール無し（生観測ダンプ＋台帳を system プロンプトへ inline）。
3. **回答パース＋採点写像**: エージェントの最終回答（`ANSWER:` 行）を既存の
   `RecallAnswer` 等に変換し、**既存 scorer をそのまま適用**（採点ロジックは不変＝独立）。
4. **live config**: `configs/experiments/s{n}_*_live.yaml`（`mode: openai`＋スナップショット固定）。
5. **射程分離**: レポートは `scope.scope_section(..., scope.agent_status_for(mode))` で
   agent 射程を「実行済み（live）」と明示。ceiling（決定的ソルバ）とは別列。

### 不変条件の遵守（§5.2）
- エージェントは **C6 ツール経由でのみ**世界を知る（B0/B1 でもシム真値に触れない）。
- 採点は oracle/scorer 側（ORコア非依存）。エージェント実装は scorer を import しない一方向。
- 全応答キャッシュ＋ temperature 0（再現性）。`mode=cache` で 0 円再生。

## 3. 対象と順序

| 優先 | シナリオ | タスク（agent 化） | 仮説 |
|------|---------|---------------------|------|
| 1（旗艦） | **S1** | 回収対象の列挙＋現在ゾーン | H2/H6 |
| 2 | S2 | 把持可否（汚染推論）の Yes/No | H5 |
| 3 | S6 | レーン選定（コスト最小化） | H4 |

S1 を旗艦として 1 本通し、パターンを確立してから S2/S6 へ横展開する
（T2/T5/T7 と同じ「1 本作って横展開」方式）。

## 4. コスト概算（`orx exp estimate` 方式・実APIを叩かない）

T2 live の実測（54 問・3 agent 条件 ≈ 334k トークン・約 $0.5）を基準に外挿:

| シナリオ | 質問数(概算) | agent 条件 | 推定トークン | 推定費用（既定単価） |
|---------|-------------|-----------|-------------|---------------------|
| S1 | 8 seed × ~4 = ~32 | OR-full/B1/B0 | ~0.2–0.4M | ~$0.2–0.5 |
| S2 | 8 seed × ~6 = ~48 | OR-full/B1/B0 | ~0.3–0.5M | ~$0.3–0.6 |
| S6 | 8 seed × ~8 = ~64 | OR-full/OR-vec/OR-sym | ~0.3–0.5M | ~$0.3–0.6 |

3 本合計の桁は **~1M トークン / ~$1–2**（既定単価。実モデル単価で前後）。
実装後は `orx exp estimate configs/experiments/s1_*_live.yaml` で正確な概算を出す。

## 5. 承認ゲート（課金実行の前提・CLAUDE.md §6）

R-3a は **(a) 新規実装（agent 条件・ツール・パース・live config）** と
**(b) 課金 live 実行** の 2 段。(b) は概算提示＋ユーザー承認が前提。

- **承認されたら**: S1 の agent 条件を実装 → `orx exp estimate` で概算提示 →
  承認 → `make exp-live`（S1）→ agent 射程の数値を ceiling/ablation と分離報告 →
  IMPROVEMENT.md R-3a をクローズ → S2/S6 へ横展開。
- **承認まで**: 本設計のみ（無課金）。R-3b–f は既に完了（`make scenario-all` の統計・掃引・恒久ログ）。

## 6. 完了基準（DoD）

S1（旗艦）で: agent 条件 3 つが実 LLM で動き、`RecallAnswer` に写像して既存 scorer で採点、
results.json に agent 射程の per_condition＋comparisons が入り、レポートが「実行済み（live）」と
表記され ceiling と分離される。全応答キャッシュで `mode=cache` 再現可能。`tests/` に
stub モードのハーネス検証（API を叩かない）を追加。

---

## 7. 横展開: S3 / S4 / S5 / S7 の agent 化（R-B, 2026-06-16）

R-3a（S1/S2/S6）で確立した共通ハーネス（`exp/agent_tools.py` ＋各 `agent.py` ＋
`runner.run_agent`）を残り 4 シナリオへ横展開した。各シナリオは **2 条件**（OR-full-llm ＋
1 baseline、トークン最小化）で、1 エピソードを 1 コールにまとめ、**蒸留済み情報のみ**を
プロンプトに与え（真値は渡さない）、既存の **oracle 真値ヘルパで採点**する。

| S | 仮説 | OR-full-llm が見る蒸留情報 | baseline-llm が欠くもの | 決定 | 採点(oracle) | 主指標(成否/連続) |
|---|------|----------------------------|--------------------------|------|--------------|-------------------|
| **S3** | H1/H3 | 語彙横断（全ベンダー）の能力契約表（宣言可搬・信頼度を正規化） | B1-llm: 基準ベンダーのみ（横断翻訳不可） | task→machine_id | `allocation_correct` | 全工程正答 / 正答率 |
| **S4** | H2/H4 | 観測ごとの位置近接＋**視覚署名コサイン**の候補表 | OR-sym-llm: 位置のみ（署名なし） | obs→asset 対応 | `is_missed_anomaly`/`required_action`＋`reconcile`(確信度加重) | 対応完全＆見逃し0 / 対応精度 |
| **S5** | H5/H6 | 区画グラフ＋**禁止規範写像**（item_class→禁止 zone class） | B1-llm: 規範写像なし（最短経路） | transport→経路(zone列) | `count_violations` | 違反0 / 違反数(↓) |
| **S7** | H2/H4/H6 | 最終目撃ゾーン＋note署名コサイン類似度（双対表現の蒸留）＋確認マージン規則 | B0-llm: ゾーンのみ（署名・所有なし） | resident→obj index / ESCALATE | `delivery_outcome` | 誤配送0 / 成功率 |

**射程の分離（各 agent レポートに明記）**: これら agent 版は、決定的アブレーションが担う
機構検証（同一性/信念/双対表現/規範のオン・オフ）とは**別射程**の「実 LLM が当該蒸留情報を
使って勝てるか」を測る。簡約（S3=静的割当・S4=対応付けに焦点・S5=規範経路に焦点）は、機構の
完全検証（オンライン較正・custody 完全再構成）が決定的版に残っていることを前提とする。

**簡約の根拠**: S6 が「H4 機構＝決定的 OR-vec/OR-sym、規制写像の agent 寄与＝agent 版」と
射程を分けたのと同じ方針。グラフ物質化（SPARQL ツール）は S1/S2 で確立済みだが、S3–S7 の
世界は小さく寄与の本体が**蒸留写像**にあるため、S6 と同じ「蒸留情報の1ショット」を採る。

### live 実行（課金・要コスト承認）
```bash
uv run orx scenario run configs/experiments/s7_ownership_live.yaml   # 個別
make scenario-run-live-all                                            # s1..s7 の *_live.yaml 一括
```
オフライン検証（無課金）は各 `tests/scenarios/s{3,4,5,7}/test_agent_s*.py`（stub・実APIなし）。

### 第2モデル再現（R-A・モデル依存性の切り分け, PROJECT.md §8.3）
主要 live 結果（T2/T5/T7・S1/S2/S6）の `*_live_m2.yaml`（llm_model のみ第2版）を用意。
第1モデルは全応答キャッシュ済みで 0 円、第2モデルのみ新規記録（課金）。
```bash
make exp-estimate-m2      # 概算（実APIを叩かない）
make live-m2-all          # T2/T5/T7・S1/S2/S6 を第2モデルで再現（要承認）
```
