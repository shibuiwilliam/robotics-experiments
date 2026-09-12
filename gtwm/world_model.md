---
paths:
  - "src/gtwm/wm/**"
  - "src/gtwm/grounding/**"
  - "configs/wm/**"
  - "configs/grounding/**"
---

# 世界モデル（C2/C3）と接地層（C4–C7, C9, C11）

## デバイスと数値
- デバイスは `get_device()` のみ（mps → cpu）。float32 固定。MPS では bf16 を使わない。`PYTORCH_ENABLE_MPS_FALLBACK=1` 前提で、fallback 警告が出た op は `docs/status.md` に記録する。
- 学習は1ラン2時間以内。`configs/wm/smoke.yaml` は3分以内で終わる（エピソード2本、K=8、d=64、2層）。
- MPS は完全決定的ではない。評価は seed 3本の平均と区間で報告し、単一 seed の数字で判断しない。

## 既定の構成（`configs/wm/base.yaml`）
- エンコーダ：`facebook/dinov2-small`（transformers、凍結、HF キャッシュ使用）＋学習可能アダプタ（線形1層）。比較用に `dinov2-base` を設定で切替可能にする。
- スロット：slot attention、K=16、D=128、反復3回。型ヘッド（pallet/case/agv/worker/equipment/none）を各スロットに付ける（オントロジー型付きスロット）。
- 多視点統合：カメラ毎のスロットを床面座標に投影して統合（`wm/fusion.py`）。カメラ内部・外部パラメータは MJCF から読む。
- 動態：Transformer 4層・d=256、入力は [スロット列, 条件トークン γ, 行動トークン]。アンサンブル3本で不確実性を出す。
- 階層フロー層（C3）：信念 KG 上の1分単位の時系列 GNN。事象境界はオントロジーのイベント（棚入れ完了など）を教師とする。
- 計画：MPPI（サンプル 256、ホライズン 20 ステップ）。候補軌道は α で記号化し、SHACL 違反の候補を除外する（シールド）。出力は助言のみ。

## インタフェース（契約。変更は ADR）
```
Encoder.encode(frames: [B,T,C,3,H,W]) -> tokens [B,T,C,P,De]
SlotModule(tokens) -> slots [B,T,K,D], type_logits [B,T,K,6]
Conditioner(subgraph: KGSubgraph) -> cond [B,Dc]
Dynamics.rollout(slots_t: [B,K,D], cond, actions: [B,h,Da] | None, h) -> [B,h,K,D]（アンサンブル次元は別引数で）
Probe(slots) -> list[Belief]  # 存在・型・位置(ゾーン+床面座標)・状態・関係(集約)
Consistency.epsilon(z_t, s_t, h) -> EpsilonRecord（ε_h とその分解）
```

## 損失
- 予測損失（潜在）、アンカー接地損失（アンカー時刻の真値のみ）、制約損失（`grounding/constraints.py` の product t-norm による違反確率の罰則）、同一性一貫性損失（ID の時間的連続）。重みは `configs/wm/base.yaml` の `loss_weights`。
- 人手ラベルに相当するものは、シミュレーションでは真値の一部（棚卸し相当：1日2回相当の時刻）だけを使い、全真値で学習しない（実サイト条件の模擬）。

## 接地層の実装規則
- α の出力は `Belief`（pydantic）：subject, predicate, object, confidence, source(wm|anchor|human|record), valid_from, transaction_time, latent_ref。RDF-star への変換は `kg/` 側で行う。
- 確信度は温度スケーリングで較正し、`eval/metrics.py` の ECE を学習後に必ず出す（目標 ≤ 0.05）。
- ε の距離 d は付録 A の重み（位置 0.4、状態 0.3、関係 0.2、型 0.1）を `configs/grounding/epsilon.yaml` から読む。コードに数値を書かない。
- 同一性解決：費用＝WM 予測位置の距離 ＋ 外観埋め込みの距離。ハンガリアン割当（scipy）。遮蔽中は予測位置で保持し、アンカーで強制再同定。
- 乖離台帳（`grounding/ledger.py`）のスキーマは計画書 5.4 の表と一致させる。状態遷移は open → confirmed/dismissed → resolved のみ。
- 概念発見（`grounding/concept_discovery.py`）：残差のクラスタリング（HDBSCAN）までは LLM なし。命名・説明の生成だけ `gtwm.llm` を呼び、結果は `gt:ConceptCandidate` として人の承認待ちにする。

## テスト
- 形状テストは CPU・極小次元で `tests/unit`。数値テストは合成系列（等速直線運動、静止＋遮蔽）で予測誤差が閾値以下になることを検査する。
- α のテストはシミュレーション真値で F1 を計算し、smoke データで 0.6 以上を最低ラインにする（本実行の目標は H1 の 0.90）。
