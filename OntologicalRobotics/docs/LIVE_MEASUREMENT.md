# LIVE_MEASUREMENT.md — H1–H7 エージェント検証の本計測 runbook

> **これは課金を伴う操作の手順書である。** ORX の数値のうち「エージェントが
> オントロジーを使って生データのエージェントに勝つ」という**仮説本体（H1/H4/H6/H7）**は、
> 実LLM/実埋め込みによる live 計測でしか測れない（IMPROVEMENT.md R-1）。本書はその
> 安全な実行手順を定める。オフライン経路（stub/cache）はこの手順なしで常に動く。

---

## 0. 前提条件（チェックリスト）

1. `OPENAI_API_KEY` が設定済み（`.env` に置く。`.env` は gitignore 済み・コミット禁止）。
2. **コスト承認**を得ている（下記の概算をユーザーに提示し、go/no-go を取る）。
3. live config のモデルスナップショット名を、組織で使う**正確な日付付き版**に更新済み
   （`configs/experiments/*_live.yaml` の `provider.llm_model` / `text_embedding_model`）。
   モデル名のハードコードは禁止（CLAUDE.md §6）。設定値は構成ハッシュ・マニフェストに焼き込まれる。

## 1. 概算（実APIを叩かない・承認判断用）

```bash
make exp-estimate-live          # T2/T5/T7 live の概算を一括表示
# 個別:
uv run orx exp estimate configs/experiments/t2_business_live.yaml
```

表示される「LLM呼び出し回数・推定トークン・推定費用レンジ」を確認する。価格前提は実モデルの
単価に合わせて上書きできる:

```bash
uv run orx exp estimate configs/experiments/t2_business_live.yaml \
  --price-in 0.15 --price-out 0.60
```

**概算の目安（既定前提）**: T2 ≈ 63万トークン / T5 ≈ 3.3万 / T7 ≈ 2.5万（埋め込み）。
合計の桁は ~0.7M トークン規模（B0 の長い inline プロンプトで上振れしうる）。

## 2. プリフライト（自動・実行前検証）

`orx exp run` は `mode=openai` の実験に対し、記録を始める前に以下を自動検証する:

- `OPENAI_API_KEY` 不在なら**最初のAPI呼び出しを待たず**に実行可能なエラーで停止する。
- 課金概算を黄色で印字してから記録に入る。

stub/cache モードでは何も起きない（オフライン経路は常に通る）。

## 3. 本計測の実行（課金）

承認後に実行する。初回は `mode=openai` で**全応答がディスクにキャッシュ**される。

```bash
make exp-live-t2     # H6, H7（業務‐物理クエリ: OR-full vs B1 vs B0）
make exp-live-t7     # H4（SOP検索: onto-guided vs vector-rag, OpenAI埋め込み）
make exp-live-t5     # H1（オンボーディング: llm支援 vs heuristic vs handwritten）
# または一括:
make exp-live-all
```

出力: `reports/exp-*-live-*.md`（射程は agent 列で報告）＋ `data/runs/exp-*/results.json`
（git commit・モデルスナップショット・構成ハッシュ・シードを焼き込み）。

## 4. 再現（0 円・キャッシュ再生）

初回記録後は、同一プロンプトはキャッシュヒットするため追加費用なしで再現できる。完全に
APIを遮断して再現したい場合は config の `provider.mode` を `cache` にして実行する（ミスは
`CacheMissError` で即座に分かる）。

```bash
# t2_business_live.yaml の provider.mode を cache に変えて:
uv run orx exp run configs/experiments/t2_business_live.yaml
```

## 5. 結果の解釈（射程の厳密分離・C1）

- live レポートの数値は **agent 射程**（実LLM/実埋め込み）にのみ計上する。
- 決定的リファレンス（OR-reference）の 1.000 は **ceiling**（表現上限）であって仮説確認ではない。
- H7（トークン効率）は `accuracy_per_1k_tokens` で条件間比較する（stub では 0 のため live 必須）。
- 可能なら第2モデルで主要結果を再現し、モデル依存性を切り分ける（PROJECT.md §8.3）。

## 6. 成功基準（PROJECT.md §12 を agent 射程で充足）

各仮説 H1/H4/H6/H7 について (a) 統計的裏付けのある結論、(b) 再現可能なリプレイ一式
（キャッシュ＋results.json）、(c) 自動生成レポート、の3点が揃えば R-1 完了。棄却も成果
（棄却条件の特定まで）。完了後 `IMPROVEMENT.md` の R-1 をクローズする。

---

## 6. シナリオ agent 横展開（S3/S4/S5/S7）と第2モデル再現（R-A/R-B, 2026-06-16）

### 6.1 残シナリオ agent live（S3/S4/S5/S7）
S1/S2/S6 と同じハーネスで agent 条件を追加した（`docs/SCENARIO_AGENT_DESIGN.md §7`）。
オフライン（stub）検証は無課金で常に通る。課金 live は要承認:

```bash
# 個別（プリフライトが mode=openai のキー有無を記録前に検証）
uv run orx scenario run configs/experiments/s3_multi_vendor_live.yaml
uv run orx scenario run configs/experiments/s4_inspection_live.yaml
uv run orx scenario run configs/experiments/s5_hospital_live.yaml
uv run orx scenario run configs/experiments/s7_ownership_live.yaml
# まとめて（s1..s7 の *_live.yaml）
make scenario-run-live-all
```
初回 mode=openai で全応答キャッシュ → 以後 `provider.mode: cache` で 0 円再現（PROJECT.md §8.3）。

### 6.2 第2モデル再現（モデル依存性の切り分け, PROJECT.md §8.3）
主要 live 結果（T2/T5/T7・S1/S2/S6）を別 LLM スナップショットで再現する。第1モデルは
キャッシュ済みのため 0 円、第2モデルのみ新規記録（課金）。`*_live_m2.yaml` の `llm_model`
を組織で使う**正確な第2スナップショット**へ更新してから実行する（既定は例の値）。

```bash
make exp-estimate-m2      # 概算（実APIを叩かない・承認判断用）
make live-m2-all          # = exp-live-m2-all（t2/t5/t7）+ scenario-live-m2-all（s1/s2/s6）
```

判断材料: 主要 live 結論（H6/H7/H4/H1・S1/S2/S6）が第2モデルでも同方向（OR-full-llm 優位・
安全違反0・コスト差）なら**モデル非依存**として頑健性が増す。差異が出たら**モデル依存**として
限界に明記する（PROJECT.md §13 リスク登録簿: モデル更新による結果漂移）。
