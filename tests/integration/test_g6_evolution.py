"""ゲート G6 の統合テスト（docs/TASK.md M6-07）。

検収内容: SynthWorld に未知概念を仕込み、**発見 → 提案 → (モック curation) → プローブ学習 →
再接地**で遡及的に事実が増えることを確認する。LLM は ``replay``。

ゲート G6 の条件:

- G5 維持
- M6-07 緑
- **LLM がホットパス（信念更新・照合・事象昇格）から呼ばれていないことの静的検査**

**「未知概念」の作り方。** SynthWorld のフルーエントのうち 1 つだけプローブを学習せず、基盤から
見て「よく起きているが名前が無い」現象にする。発見ループがそれを見つけ、クラスタ所属を弱ラベルに
してプローブを学習し、過去の潜在に適用して事実を増やせるか——これが M6 の主張である。

**この合成世界での限界を先に書く。** 仕込む概念は ``Accessible`` にした。``VibrationAnomaly`` は
87.5% の時間成立する単一区間（02:41–06:11）で、保留窓に負例が無く評価できない。そして
``Accessible`` は SynthWorld では実質的に ``Running`` の否定である——つまり発見されるクラスタは
「Running プローブが反応しない領域」とほぼ一致し、**この世界では発見がやや自明になる**。
機構（低被覆クラスタ → 弱ラベル → プローブ → 再接地）が通ることは確かめられるが、
「独立した未知概念を見つけた」ことの証明にはならない。実機データでの再測定が必要である。

**真値を弱ラベルに使わないのが要点である。** 新しいプローブはクラスタ所属（機械が見つけた
まとまり）だけで学習する。真値との照合は**評価のためだけ**に行う（不変条件 10 の範囲で
``sbp.testing`` 経由）。真値を学習に使うと「発見できた」ことの証明にならない。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.llm import (
    LlmMode,
    LlmTask,
    Message,
    PromptRegistry,
    ReplayProvider,
    Role,
    load_routing,
)
from sbp.reasoning import (
    ConceptProposal,
    DiscoveryTrigger,
    ProposalStatus,
    cluster_latents,
    find_low_coverage_clusters,
)
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from sbp.knowledge import AssertionStore
    from sbp.reasoning import DiscoverySignal

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "llm"
PUMP = "https://sbp.example/site/siteA/asset/P-101"
VERSION = vocab.ONTOLOGY_VERSION
HIDDEN_FLUENT = Fluent.ACCESSIBLE
"""仕込む「未知概念」。このテストではこの概念のプローブを学習しない。

``Accessible`` を選んだのは、保留窓に両クラスが入る唯一のフルーエントだからである
（``VibrationAnomaly`` は保留窓が全て真で AUROC が定義できない）。"""

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "definition": {"type": "string"},
        "superclass": {"type": "string"},
        "mutually_exclusive_with": {"type": "array", "items": {"type": "string"}},
        "initiates": {"type": "array", "items": {"type": "string"}},
        "terminates": {"type": "array", "items": {"type": "string"}},
        "rejected_alternatives": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "definition", "rejected_alternatives"],
}


def render_messages(prompt_body: str, signals: tuple[DiscoverySignal, ...]) -> list[Message]:
    """LLM へのメッセージを**決定的に**組み立てる。

    fixture の鍵はメッセージの内容ハッシュなので、ここが非決定的だと fixture が当たらない。
    クラスタのサイズと活性だけを渡し、潜在ベクトルそのものは渡さない——数値の羅列は LLM の
    役に立たず、ハッシュを不安定にするだけである。

    Args:
        prompt_body: プロンプト本文。
        signals: 発見 signal。
    """
    lines = [f"- {signal.trigger.value}: {signal.note}" for signal in signals]
    existing = ", ".join(
        sorted(name for name in ("Running", "UnderMaintenance", "Faulted", "Accessible"))
    )
    return [
        Message(role=Role.SYSTEM, content=prompt_body),
        Message(
            role=Role.USER,
            content=(
                "既存のフルーエント型: " + existing + "\n発見された signal:\n" + "\n".join(lines)
            ),
        ),
    ]


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def latents(world: SynthWorld) -> np.ndarray:
    """Koopman 潜在（これが発見の舞台）。"""
    cut = int(len(world.timestamps) * 0.6)
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/model/koopman",
        version="1.0.0",
        domain=vocab.ent.Pump,
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    ).fit(world.observations[:cut], seed=2026)
    return model.encode_series(world.observations)


@pytest.fixture(scope="module")
def discovery(world: SynthWorld, latents: np.ndarray) -> dict[str, Any]:
    """発見ループを回す（設計書 §8.6 の段階 1・2）。

    既知概念のプローブが反応していない領域を探す。``Running`` プローブは稼働中に高く出るので、
    「停止中かつ振動異常」の領域は活性が低く、かつ潜在空間でまとまっている。
    """
    labels = world.oracle.labels_for(Fluent.RUNNING)
    cut = int(len(labels) * 0.6)
    probe = LinearProbe(dim=latents.shape[1], seed=2026)
    probe.fit(latents[:cut], labels[:cut], epochs=500)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
    running = probe.predict(latents).probabilities

    # 既知プローブの「最大活性」。1 本しか無いので Running の出力そのもの
    activations = running
    # 潜在のスケールに合わせて半径を決める（固定値だと次元・スケール依存になる）
    spread = float(np.mean(np.std(latents, axis=0)))
    clusters = cluster_latents(latents, activations, radius=spread * 2.0, min_size=20)
    signals = find_low_coverage_clusters(clusters, activation_threshold=0.3, entity=PUMP)
    return {"clusters": clusters, "signals": signals, "activations": activations}


class TestG6Discovery:
    """設計書 §8.6 の段階 1・2: 低被覆・高密度クラスタを見つける。"""

    def test_clusters_are_found(self, discovery: dict[str, Any]) -> None:
        assert discovery["clusters"], "潜在がまとまりを持っていない（半径の見積りが外れている）"

    def test_a_low_coverage_cluster_is_signalled(self, discovery: dict[str, Any]) -> None:
        """プローブが反応しない領域はどの指標にも現れない。それを見つけるのが発見ループ。"""
        signals = discovery["signals"]
        assert signals, "低被覆クラスタが見つからない"
        assert signals[0].trigger is DiscoveryTrigger.LOW_COVERAGE_CLUSTER
        assert signals[0].cluster is not None
        assert signals[0].cluster.size >= 20

    def test_the_signal_points_at_the_hidden_concept(
        self, discovery: dict[str, Any], world: SynthWorld
    ) -> None:
        """見つけたクラスタが、実際に仕込んだ未知概念の領域に当たっているか。

        **この照合は評価のためだけに行う**（``sbp.testing`` 経由）。発見そのものは真値を見ていない。
        """
        truth = world.oracle.labels_for(HIDDEN_FLUENT)
        members = discovery["signals"][0].cluster.member_indices
        hit_rate = float(np.mean(truth[list(members)]))
        base_rate = float(np.mean(truth))
        assert hit_rate > base_rate, (
            f"クラスタ内の未知概念の割合 {hit_rate:.3f} が全体 {base_rate:.3f} を上回らない"
        )


class TestG6Proposal:
    """設計書 §8.6 の段階 3・4: LLM 提案と curation。LLM は replay。"""

    @pytest.fixture
    def proposal(self, discovery: dict[str, Any]) -> ConceptProposal:
        routing = load_routing()
        entry = routing[LlmTask.CONCEPT_PROPOSAL]
        template = PromptRegistry().get(LlmTask.CONCEPT_PROPOSAL, entry.prompt_version)
        messages = render_messages(template.body, discovery["signals"])
        provider = ReplayProvider(mode=LlmMode.REPLAY, root=FIXTURE_ROOT)
        response = provider.complete_structured(
            messages,
            schema=PROPOSAL_SCHEMA,
            task=LlmTask.CONCEPT_PROPOSAL,
            prompt_version=template.version,
        )
        content = response.content
        return ConceptProposal(
            name=content["name"],
            definition=content["definition"],
            superclass=content.get("superclass", ""),
            mutually_exclusive_with=tuple(content.get("mutually_exclusive_with", ())),
            initiates=tuple(content.get("initiates", ())),
            terminates=tuple(content.get("terminates", ())),
            rejected_alternatives=tuple(content["rejected_alternatives"]),
            signals=discovery["signals"],
            proposed_at=now(),
            llm_provenance=response.provenance(),
        )

    def test_the_proposal_comes_from_a_fixture(self, proposal: ConceptProposal) -> None:
        """LLM はホットパスに無く、テストではネットワークを使わない。"""
        assert proposal.llm_provenance["provider"]
        assert "@" in proposal.llm_provenance["prompt_version"]

    def test_the_proposal_cites_its_signals(self, proposal: ConceptProposal) -> None:
        assert proposal.signals
        assert proposal.rejected_alternatives

    def test_nothing_happens_before_curation(self, proposal: ConceptProposal) -> None:
        """自動採用禁止（設計書 §8.6 の段階 4、CLAUDE.md）。"""
        assert proposal.status is ProposalStatus.PROPOSED
        assert not proposal.needs_ontology_bump

    def test_acceptance_requests_an_ontology_bump(self, proposal: ConceptProposal) -> None:
        accepted = proposal.curate(
            ProposalStatus.ACCEPTED, note="概念オーナー（モック）が採用。定義は観測可能"
        )
        assert accepted.needs_ontology_bump
        assert accepted.curation_note


class TestG6GroundingTheNewConcept:
    """設計書 §8.6 の段階 5: クラスタ所属を弱ラベルにしてプローブを学習する。"""

    @classmethod
    @pytest.fixture(scope="class")
    def trained(cls, discovery: dict[str, Any], latents: np.ndarray) -> dict[str, Any]:
        """クラスタ所属だけを教師にプローブを学習する（**真値は使わない**）。"""
        members = set(discovery["signals"][0].cluster.member_indices)
        weak_labels = np.array([index in members for index in range(latents.shape[0])], dtype=bool)
        cut = int(latents.shape[0] * 0.6)
        probe = LinearProbe(dim=latents.shape[1], seed=2026)
        probe.fit(latents[:cut], weak_labels[:cut], epochs=500)
        probe.attach_calibrator(
            TemperatureScaler().fit(probe.logits(latents[:cut]), weak_labels[:cut])
        )
        return {"probe": probe, "weak_labels": weak_labels, "cut": cut}

    def test_the_probe_learns_the_cluster(
        self, trained: dict[str, Any], latents: np.ndarray
    ) -> None:
        """弱ラベル（クラスタ所属）を再現できること。"""
        probe = trained["probe"]
        cut = trained["cut"]
        metrics = probe.evaluate(
            latents[cut:], trained["weak_labels"][cut:], alignment_residual=0.0
        )
        assert metrics.auroc >= 0.85, f"弱ラベルすら再現できない: AUROC {metrics.auroc:.4f}"

    def test_the_new_probe_detects_the_hidden_concept(
        self, trained: dict[str, Any], latents: np.ndarray, world: SynthWorld
    ) -> None:
        """**M6 の主張。** 真値を見ずに学習したプローブが、仕込んだ未知概念を当てる。

        真値との照合は評価のためだけに行う（``sbp.testing`` 経由）。
        """
        truth = world.oracle.labels_for(HIDDEN_FLUENT)
        cut = trained["cut"]
        metrics = trained["probe"].evaluate(latents[cut:], truth[cut:], alignment_residual=0.0)
        assert metrics.auroc > 0.6, (
            f"発見した概念が未知概念と対応していない: AUROC {metrics.auroc:.4f}"
        )


class TestG6Regrounding:
    """設計書 §8.6 の段階 6: 過去の潜在に適用して遡及的に事実を増やす。"""

    def test_facts_are_added_retroactively(
        self,
        discovery: dict[str, Any],
        latents: np.ndarray,
        world: SynthWorld,
        assertion_store: AssertionStore,
    ) -> None:
        """新しいプローブを**過去の**潜在に当て、以前は無かった事実を追加する。

        要点は **``valid_from`` が過去で ``tx_from`` が今** であること（二時制）。「いま分かった
        過去のこと」を、過去に分かっていたことにしない。
        """
        members = set(discovery["signals"][0].cluster.member_indices)
        weak_labels = np.array([index in members for index in range(latents.shape[0])], dtype=bool)
        cut = int(latents.shape[0] * 0.6)
        probe = LinearProbe(dim=latents.shape[1], seed=2026)
        probe.fit(latents[:cut], weak_labels[:cut], epochs=500)
        probe.attach_calibrator(
            TemperatureScaler().fit(probe.logits(latents[:cut]), weak_labels[:cut])
        )

        # 過去の窓（学習に使った前半）へ適用する = 再接地
        past_probabilities = probe.predict(latents[:cut]).probabilities
        reground_at = now().replace(microsecond=0)
        predicate = vocab.ent.Accessible
        added = 0
        for index in range(0, cut, 60):  # 10 分ごとに代表点を 1 つ
            probability = float(past_probabilities[index])
            if probability < 0.7:
                continue
            assertion_store.assert_(
                Assertion(
                    subject=PUMP,
                    predicate=predicate,
                    object=True,
                    valid_from=world.timestamps[index],
                    tx_from=reground_at,
                    modality=Modality.INFERRED,
                    belief=BooleanBelief(p=min(0.99, probability)),
                    provenance="regrounding:g6",
                    ontology_version=VERSION,
                )
            )
            added += 1

        assert added > 0, "再接地で 1 件も事実が増えなかった"

        # 再接地後の取引時刻では見える
        after = assertion_store.query(
            subject=PUMP,
            predicate=predicate,
            as_of=reground_at + timedelta(seconds=1),
        )
        assert len(after) == added

        # **再接地前の取引時刻では見えない**（二時制の要点）
        before = assertion_store.query(
            subject=PUMP,
            predicate=predicate,
            as_of=reground_at - timedelta(hours=1),
        )
        assert before == [], "再接地前の時点で新しい事実が見えてはならない"


class TestG6HotPathStaticCheck:
    """ゲート G6 の条件 3: LLM がホットパスから呼ばれていないことの静的検査。"""

    def test_the_import_linter_contract_exists(self) -> None:
        """契約がある（= CI が機械検査している）こと。"""
        config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "LLM をホットパス" in config
        assert "sbp.reasoning.belief" in config
        assert "sbp.bridge.lifting" in config

    def test_the_hot_path_modules_do_not_import_llm(self) -> None:
        """契約の対象モジュールが実際に ``sbp.llm`` を import していないこと。

        import-linter が ``make check`` で検査しているが、ここでも直接確かめる——契約が
        誤って外されたことに気付けるようにするため。
        """
        hot_paths = [
            REPO_ROOT / "src/sbp/reasoning/belief.py",
            REPO_ROOT / "src/sbp/reasoning/coupling.py",
            REPO_ROOT / "src/sbp/reasoning/rules.py",
            REPO_ROOT / "src/sbp/bridge/lifting.py",
            REPO_ROOT / "src/sbp/bridge/probes.py",
            REPO_ROOT / "src/sbp/bridge/calibration.py",
        ]
        for path in hot_paths:
            body = path.read_text(encoding="utf-8")
            assert "sbp.llm" not in body, f"{path.name} が LLM を参照している（不変条件 11）"
        for path in (REPO_ROOT / "src/sbp/reasoning/reconciliation").glob("**/*.py"):
            assert "sbp.llm" not in path.read_text(encoding="utf-8"), (
                f"{path.name} が LLM を参照している（不変条件 11）"
            )

    def test_the_llm_default_mode_is_replay(self) -> None:
        """既定が live だと、テストが黙ってネットワークに出る。"""
        assert Settings().llm_mode.value == "replay"

    def test_the_fixture_used_by_this_test_is_committed(self) -> None:
        """fixture がリポジトリにあること（無ければ replay が失敗する）。"""
        entry = load_routing()[LlmTask.CONCEPT_PROPOSAL]
        template = PromptRegistry().get(LlmTask.CONCEPT_PROPOSAL, entry.prompt_version)
        # signals に依らない固定の digest 検査はできないので、ディレクトリの存在だけ見る
        directory = FIXTURE_ROOT / LlmTask.CONCEPT_PROPOSAL.value
        assert directory.is_dir(), f"{directory} が無い"
        assert list(directory.glob("*.json")), "概念提案の fixture が 1 件も無い"
        assert template.version
