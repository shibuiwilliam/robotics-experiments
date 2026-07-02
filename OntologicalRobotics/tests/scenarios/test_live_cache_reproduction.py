"""live(S1/S2/S6) agent 結果の **mode=cache 0 円再現ゲート**（R-1B）。

記録済み live 結果（`docs/LIVE_RESULTS.md`）が、記録済みディスクキャッシュ（`data/cache/llm`）から
**実 API を一切叩かずに**再現でき、主要な結論（headline invariants）が回帰していないことを検証する。

- **課金しない**: provider.mode を `cache` に強制（CacheLLMClient はミス時に CacheMissError を投げ、
  実 API を絶対に呼ばない）。
- **キャッシュ不在の checkout では skip**: `data/cache/llm` はコミットされない
  （CLAUDE.md §5・data/ は gitignore）。キャッシュが無い CI/clone ではキャッシュミス →
  `pytest.skip` で**明示スキップ**（沈黙合格でも実API呼び出しでもない）。
  キャッシュがあるローカルでは実際に回帰を検出する。
- 断言は脆い厳密値ではなく**結論の不等式・ゼロ違反**（モデル更新・丸めに頑健）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orx.common.providers import CacheMissError
from orx.exp import scenario as scn

_CACHE_DIR = Path("data/cache/llm")


def _run_cached(config_name: str) -> dict:
    """live config を mode=cache で実行して results を返す。キャッシュ無ければ skip。"""
    if not _CACHE_DIR.exists() or not any(_CACHE_DIR.rglob("*.json")):
        pytest.skip(f"LLM キャッシュ無し（{_CACHE_DIR}）— このゲートは記録済みローカルでのみ作動")
    cfg_path = Path("configs/experiments") / config_name
    if not cfg_path.exists():
        pytest.skip(f"{cfg_path} が無い")
    config = scn.load_scenario_experiment(cfg_path)
    base = config.provider
    from orx.common.providers import ProviderConfig

    prov = (base or ProviderConfig()).model_copy(update={"mode": "cache"})
    config = config.model_copy(update={"provider": prov})
    spec = scn.get(config.scenario)
    try:
        return spec.run(config, Path("/tmp"), lambda *a: None)
    except CacheMissError as exc:  # この checkout のキャッシュは当該プロンプトを含まない
        pytest.skip(f"cache miss（記録時とプロンプト不一致 or 未記録）: {exc}")


def test_s1_live_cache_reproduces_completion() -> None:
    """S1: OR-full-llm は回収完遂 1.0、ベースラインは 0.5（H2/H6/H7・搬送中個体の解決）。"""
    res = _run_cached("s1_lot_recall_live.yaml")
    assert res["scope"] == "agent"
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["completion"] >= 0.99
    for b in ("B1-llm", "B0-llm"):
        assert pc["OR-full-llm"]["completion"] > pc[b]["completion"] + 1e-9


def test_s2_live_cache_reproduces_zero_safety_violations() -> None:
    """S2: OR-full-llm は安全違反 0、ベースラインは違反多数（H5・汚染推論）。"""
    res = _run_cached("s2_allergen_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["safety_violations"] == 0
    for b in ("B1-llm", "B0-llm"):
        assert pc[b]["safety_violations"] > pc["OR-full-llm"]["safety_violations"]


def test_s6_live_cache_reproduces_cost_advantage() -> None:
    """S6: OR-full-llm は平均コスト < B0-llm かつ高コスト誤り 0（規制ルーティング）。"""
    res = _run_cached("s6_recycling_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["high_cost_errors"] == 0
    assert pc["OR-full-llm"]["mean_cost"] < pc["B0-llm"]["mean_cost"]


# --- S3/S4/S5/S7 agent live（2026-06-17 記録）。一様な勝ちではなく live でしか分からない
#     条件付き/null/限界も含む（PROJECT.md §12）。断言は真かつ再現する事実のみ ---


def test_s3_live_cache_reproduces_cross_vendor_win() -> None:
    """S3【positive】: 語彙横断の能力表で OR-full-llm が適格機体を選び、基準ベンダーのみの B1-llm は
    段取替後（横断要）の品種に届かず失敗（H1/H3 の agent 射程を live 実証）。"""
    res = _run_cached("s3_multi_vendor_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["overall_accuracy"] > pc["B1-llm"]["overall_accuracy"]
    assert pc["OR-full-llm"]["setup_accuracy"] > pc["B1-llm"]["setup_accuracy"]  # 1.0 > 0.0


def test_s4_live_cache_reproduces_signature_anchor_win() -> None:
    """S4【positive】: 視覚署名で OR-full-llm は対応付け完全・見逃し0、位置のみの OR-sym-llm は
    取り違えて異常を見逃す（H2/H4 の agent 射程を live 実証）。"""
    res = _run_cached("s4_inspection_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["anchor_accuracy"] > pc["OR-sym-llm"]["anchor_accuracy"]
    assert pc["OR-full-llm"]["missed_anomalies"] < pc["OR-sym-llm"]["missed_anomalies"]


def test_s5_live_cache_or_full_compliant_but_norm_map_null() -> None:
    """S5【null】: OR-full-llm は違反0（適合）。ただし**規範写像を持たない B1-llm も違反0**で、
    規範オントロジーの agent 射程での上積みは**この LLM では現れない**（LLM が一般常識で公共区画を
    回避するため）。live でしか分からない条件付き/null 結果を固定（PROJECT.md §12）。"""
    res = _run_cached("s5_hospital_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["violations"] == 0  # 適合は満たす
    # null を明示的に固定: B1-llm も違反0（規範写像の上積みが現れない）。偽の不等式は主張しない。
    assert pc["B1-llm"]["violations"] == 0


def test_s5_domain_norm_norm_map_value_reappears() -> None:
    """S5 ドメイン固有規制の境界【follow-up】: LLM 常識に無い架空規制では、規範写像を持つ
    OR-full-llm は違反0、持たない B1-llm は最短で禁止区画を通過し違反 > 0。S5 null は常識的規制に
    限った境界で、OR の規範オントロジーは LLM 事前知識に無い規制で価値が立つことを live で実証。"""
    res = _run_cached("s5_domain_norm_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["violations"] == 0  # 規範写像で架空規制を回避
    assert pc["B1-llm"]["violations"] > 0  # 規範写像なしは架空規制を知らず違反


def test_s7_live_cache_or_full_beats_b0_but_not_zero_misdelivery() -> None:
    """S7【mixed】: OR-full-llm は B0-llm より自動成功が高い（双対表現の蒸留が効く）が、**実 LLM は
    低マージン時の確認(X5)規則を完全には守らず誤配送 0 を達成しない**（決定的 OR-full の安全保証は
    プロンプト依存ではエージェントに移らない）。真な事実のみ固定: 成功率は B0 超・B0 は全件委譲。"""
    res = _run_cached("s7_ownership_live.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["success_rate"] > pc["B0-llm"]["success_rate"]  # 0.27 > 0.0
    assert pc["B0-llm"]["escalation_rate"] >= 0.99  # B0 は手掛かり不足で全件委譲
    # 注: OR-full-llm の誤配送は 0 でない（≈0.05）。プロンプト依存では安全が agent に移らない。


def test_s7_guard_transfers_safety_to_agent() -> None:
    """S7 ツール側ガード【follow-up】: 低マージン配送をシステム強制で ESCALATE に上書きすると、
    決定的版の安全（誤配送0）が**エージェントにも移る**。中難度 sep=0.6 では誤配送を 0 にしつつ
    高マージンの自動配送（成功 > 0）を保つ＝「安全な自動化」（H4/H6・安全）。"""
    res = _run_cached("s7_guard_live.yaml")
    pc = res["per_condition"]
    g, u, b0 = pc["OR-full-llm-guarded"], pc["OR-full-llm"], pc["B0-llm"]
    assert g["misdelivery_rate"] == 0.0  # ガードは誤配送を機械的に 0 にする（安全移植）
    assert g["misdelivery_rate"] < u["misdelivery_rate"]  # ガード無し(≈0.018)より厳密に低い
    assert g["success_rate"] > b0["success_rate"]  # 高マージンの自動配送は保つ（B0=0 を上回る）


# --- 第2モデル再現（gpt-5.4-2026-03-05・2026-06-18 記録）。モデル依存/非依存を真な事実のみで固定。
#     第1モデルはキャッシュ済（別キー）で不変。$0 再現（PROJECT.md §8.3・§13 model drift） ---


def test_s1_m2_model_independent() -> None:
    """S1【model-independent】: 第2モデルでも OR-full-llm 完遂 1.0 > baseline。結論不変。"""
    res = _run_cached("s1_lot_recall_live_m2.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["completion"] >= 0.99
    for b in ("B1-llm", "B0-llm"):
        assert pc["OR-full-llm"]["completion"] > pc[b]["completion"] + 1e-9


def test_s2_m2_model_independent() -> None:
    """S2 H5【model-independent】: 第2モデルでも OR-full-llm 安全違反 0 < baseline。結論不変。"""
    res = _run_cached("s2_allergen_live_m2.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["safety_violations"] == 0
    for b in ("B1-llm", "B0-llm"):
        assert pc[b]["safety_violations"] > 0


def test_s6_m2_is_model_dependent() -> None:
    """S6 規制ルーティング【model-DEPENDENT・重要】: 第1モデル(mini)では OR-full-llm が低コスト、
    **強い第2モデル(gpt-5.4)では B0-llm が OR-full-llm 以下のコスト**・両者とも高コスト誤り0 —
    規制写像のコスト優位は再現しない（強モデルは規制を自前知識で吸収＝S5 null と同型）。
    PROJECT.md §13「モデル更新による結果漂移」を実証。優位の反転を honest に固定。"""
    res = _run_cached("s6_recycling_live_m2.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["high_cost_errors"] == 0
    assert pc["B0-llm"]["high_cost_errors"] == 0
    # 第2モデルでは m1 の優位が反転（B0 のコストが OR-full 以下）＝モデル依存を明示固定。
    assert pc["B0-llm"]["mean_cost"] <= pc["OR-full-llm"]["mean_cost"]


def test_s6_m3_nano_too_weak_to_route() -> None:
    """S6 能力境界【nano・非単調を固定】: 最弱 nano は両条件とも全件委譲（throughput 0）。
    規制写像は効かない（route しないため mean_cost 同値）。OR 規範写像の価値は能力に非単調
    （nano:無効 / mini:有効 / full:冗長）の中位スイートスポット。境界の弱端を固定。"""
    res = _run_cached("s6_recycling_live_m3.yaml")
    pc = res["per_condition"]
    assert pc["OR-full-llm"]["throughput"] == 0.0  # 弱すぎて確信を持って route できず全件委譲
    assert pc["B0-llm"]["throughput"] == 0.0
    assert pc["OR-full-llm"]["mean_cost"] == pc["B0-llm"]["mean_cost"]  # 規制写像の効果差なし


# --- S8 キネティック層 agent（live・K2 記録済み 2026-06-27・gpt-5.4-mini）。
#     実 LLM がアクション型（送信基準のハード強制＋来歴付き書戻し）で安全・完遂・監査・可逆を達成
#     することを live で実証。アサートは真かつ再現する事実のみ（PROJECT.md §12・H8）。


def test_s8_live_cache_kinetic_win() -> None:
    """S8【K2・H8 本体】: 実 LLM＋アクション型の OR-full-llm は、送信基準（能力・規範・所有権）の
    ハード強制と来歴付き custody により **完遂1.0・安全違反0・誤配送0・監査完全1.0** を達成し、
    共通オントロジーを欠くベースライン（misdeliver・監査0）に勝つ。プロンプト順守に依存しない
    ツール側ゲートゆえ、guarded も同様に安全0・誤配送0（S7 の教訓のアクション版）。"""
    res = _run_cached("s8_fulfillment_live.yaml")
    assert res["scope"] == "agent"
    pc = res["per_condition"]
    orf, guarded = pc["OR-full-llm"], pc["OR-full-llm-guarded"]
    # OR-full-llm: 安全・完遂・監査・可逆をすべて達成
    assert orf["safety_violations"] == 0
    assert orf["misdeliveries"] == 0
    assert orf["completion"] >= 0.99
    assert orf["audit_completeness"] >= 0.99
    assert orf["recovery_rate"] >= 0.99  # 注入誤動作を ontology+custody で回復（H8 可逆性）
    # ベースラインは共通オントロジーを欠き誤配送＋監査不能（来歴語彙なし）
    for b in ("B1-llm", "B0-llm"):
        assert pc[b]["misdeliveries"] >= 1
        assert pc[b]["audit_completeness"] == 0.0
        assert pc[b]["recovery_rate"] == 0.0
        assert orf["completion"] > pc[b]["completion"] + 1e-9
    # ツール側ハードゲート: guarded は安全違反0・誤配送0
    assert guarded["safety_violations"] == 0
    assert guarded["misdeliveries"] == 0


def test_s8_m2_model_independent() -> None:
    """S8【model-independent】: 第2モデル（gpt-5.4-2026-03-05）でも OR-full-llm は完遂1.0・安全0・
    誤配送0・監査1.0・回復1.0、ベースラインは誤配送・監査0。H8 のキネティック結論は世代の異なる
    第2モデルでも同方向＝外的妥当性（PROJECT.md §8.3）。真かつ再現する事実のみ固定。"""
    res = _run_cached("s8_fulfillment_live_m2.yaml")
    pc = res["per_condition"]
    orf = pc["OR-full-llm"]
    assert orf["safety_violations"] == 0
    assert orf["misdeliveries"] == 0
    assert orf["completion"] >= 0.99
    assert orf["audit_completeness"] >= 0.99
    for b in ("B1-llm", "B0-llm"):
        assert pc[b]["audit_completeness"] == 0.0
        assert orf["completion"] > pc[b]["completion"] + 1e-9


def test_s8_m3_nano_kinetic_holds_across_capability_band() -> None:
    """S8 能力境界【第3モデル・nano】: 最弱 nano でも OR-full-llm は完遂1.0・安全0・誤配送0・
    監査1.0・回復1.0。**H8 の便益は能力帯（nano/mini/full）全域で model-independent**——S6 の
    規制ルーティング（能力依存・非単調）と異なり、安全・監査・可逆をアクション層が機械的に保証し、
    共通オントロジーが正規化知識を直接与えるため弱モデルでも崩れない（S6 と対照的な境界）。"""
    res = _run_cached("s8_fulfillment_live_m3.yaml")
    pc = res["per_condition"]
    orf = pc["OR-full-llm"]
    assert orf["completion"] >= 0.99  # 弱モデルでも遂行 1.0（提供知識＋ハードゲートで頑健）
    assert orf["safety_violations"] == 0
    assert orf["misdeliveries"] == 0
    assert orf["audit_completeness"] >= 0.99
    assert orf["recovery_rate"] >= 0.99
    for b in ("B1-llm", "B0-llm"):
        assert pc[b]["audit_completeness"] == 0.0
        assert orf["completion"] > pc[b]["completion"] + 1e-9
