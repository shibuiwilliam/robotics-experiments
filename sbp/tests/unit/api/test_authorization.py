"""ABAC のテスト（M7-06）。

検証の軸: 既定が拒否であること、3 軸がそれぞれ独立に効くこと、人に関わる概念では**許可が
あっても**個人特定に繋がるフィールドを返さないこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from sbp.api.authorization import (
    AuthorizationError,
    Permission,
    Principal,
    PrivacyClass,
    authorize,
    require_authorized,
    site_of_graph,
    visible_rows,
)
from sbp.core.modality import Modality

_RUNNING = "https://sbp.example/ont/ent#Running"
_LOCATED = "https://sbp.example/ont/ent#locatedIn"
_SITE_A = "siteA"
_SITE_B = "siteB"


def _operator(*, sites: frozenset[str] = frozenset({_SITE_A})) -> Principal:
    """siteA の稼働状態だけを見られる担当者。"""
    return Principal(
        subject="https://sbp.example/id/person/op1",
        roles=frozenset({"operator"}),
        permissions=(
            Permission(
                concept_types=frozenset({_RUNNING}),
                modalities=frozenset({Modality.OBSERVED, Modality.INFERRED}),
                sites=sites,
            ),
        ),
    )


class TestDefaultDeny:
    """既定は拒否。"""

    def test_principal_without_permissions_is_denied(self) -> None:
        """許可を 1 つも持たない要求者は何も読めない。"""
        bare = Principal(subject="https://sbp.example/id/person/new")
        decision = authorize(bare, concept_type=_RUNNING, modality=Modality.OBSERVED, site=_SITE_A)
        assert not decision.allowed
        assert "既定は拒否" in decision.reason

    def test_denial_names_all_three_axes(self) -> None:
        """拒否の理由に 3 軸が全部出る（監査で「どの軸で落ちたか」を追えるように）。"""
        decision = authorize(
            _operator(), concept_type=_LOCATED, modality=Modality.OBSERVED, site=_SITE_A
        )
        assert not decision.allowed
        assert _LOCATED in decision.reason
        assert "observed" in decision.reason
        assert _SITE_A in decision.reason

    def test_empty_permission_axis_is_rejected_at_construction(self) -> None:
        """軸が空の許可は作れない（全拒否にも全許可にも読めてしまう）。"""
        with pytest.raises(AuthorizationError, match="at least one"):
            Permission(
                concept_types=frozenset(),
                modalities=frozenset({Modality.OBSERVED}),
                sites=frozenset({_SITE_A}),
            )

    def test_require_authorized_raises(self) -> None:
        """ハンドラ向けの入口は例外にする。"""
        with pytest.raises(AuthorizationError):
            require_authorized(
                _operator(), concept_type=_LOCATED, modality=Modality.OBSERVED, site=_SITE_A
            )


class TestThreeAxes:
    """3 軸がそれぞれ独立に効く。"""

    def test_allowed_combination_passes(self) -> None:
        """3 軸すべて揃えば通る。"""
        assert authorize(
            _operator(), concept_type=_RUNNING, modality=Modality.OBSERVED, site=_SITE_A
        ).allowed

    def test_other_site_is_denied(self) -> None:
        """概念型と様相が合っていても、別拠点は読めない。"""
        assert not authorize(
            _operator(), concept_type=_RUNNING, modality=Modality.OBSERVED, site=_SITE_B
        ).allowed

    def test_other_modality_is_denied(self) -> None:
        """観測は読めても計画は読めない——別の様相は別の権限である。"""
        assert not authorize(
            _operator(), concept_type=_RUNNING, modality=Modality.PLANNED, site=_SITE_A
        ).allowed

    def test_wildcard_concept_still_respects_site(self) -> None:
        """``*`` は概念型の軸だけを開ける。拠点の軸は開かない。"""
        admin = Principal(
            subject="https://sbp.example/id/person/admin",
            permissions=(
                Permission(
                    concept_types=frozenset({"*"}),
                    modalities=frozenset(Modality),
                    sites=frozenset({_SITE_A}),
                ),
            ),
        )
        assert authorize(
            admin, concept_type=_LOCATED, modality=Modality.NORMATIVE, site=_SITE_A
        ).allowed
        assert not authorize(
            admin, concept_type=_LOCATED, modality=Modality.NORMATIVE, site=_SITE_B
        ).allowed


class TestAgentRestrictions:
    """エージェントは仮説を読めない。"""

    def test_agent_cannot_read_hypothetical_even_when_permitted(self) -> None:
        """許可に hypothetical が入っていてもエージェントには返さない。

        シナリオは検討中の案であり、エージェントが読めると「決まったこと」として行動の前提に
        入る（不変条件 3 の隔離の趣旨）。
        """
        agent = Principal(
            subject="https://sbp.example/id/agent/planner",
            permissions=(
                Permission(
                    concept_types=frozenset({"*"}),
                    modalities=frozenset(Modality),
                    sites=frozenset({_SITE_A}),
                ),
            ),
            is_agent=True,
        )
        assert authorize(
            agent, concept_type=_RUNNING, modality=Modality.PREDICTED, site=_SITE_A
        ).allowed
        denied = authorize(
            agent, concept_type=_RUNNING, modality=Modality.HYPOTHETICAL, site=_SITE_A
        )
        assert not denied.allowed
        assert "hypothetical" in denied.reason

    def test_human_with_permission_can_read_hypothetical(self) -> None:
        """人は許可があればシナリオを読める（検討するのが仕事である）。"""
        reviewer = Principal(
            subject="https://sbp.example/id/person/planner",
            permissions=(
                Permission(
                    concept_types=frozenset({"*"}),
                    modalities=frozenset({Modality.HYPOTHETICAL}),
                    sites=frozenset({_SITE_A}),
                ),
            ),
        )
        assert authorize(
            reviewer, concept_type=_RUNNING, modality=Modality.HYPOTHETICAL, site=_SITE_A
        ).allowed


class TestPersonalRedaction:
    """人に関わる概念は許可があっても遮蔽する（不変条件 9）。"""

    def test_personal_fields_are_redacted_despite_permission(self) -> None:
        """許可を持つ要求者にも外観埋め込みは返さない。

        **ここが「許可があれば見せる」にしていない唯一の箇所である。** 許可の設定ミスが
        そのまま個人情報の漏洩になるため、許可の外側で止める。
        """
        guard = Principal(
            subject="https://sbp.example/id/person/guard",
            permissions=(
                Permission(
                    concept_types=frozenset({"*"}),
                    modalities=frozenset(Modality),
                    sites=frozenset({_SITE_A}),
                ),
            ),
        )
        decision = authorize(
            guard,
            concept_type="https://sbp.example/ont/ent#PersonTrack",
            modality=Modality.OBSERVED,
            site=_SITE_A,
            privacy=PrivacyClass.PERSONAL,
            requested_fields=("track_id", "appearance_embedding", "zone", "employee_id"),
        )
        assert decision.allowed
        assert decision.is_redacted
        assert set(decision.redacted_fields) == {"appearance_embedding", "employee_id"}

    def test_operational_concepts_are_not_redacted(self) -> None:
        """設備の概念では遮蔽しない（遮蔽を広げすぎると使えない API になる）。"""
        decision = authorize(
            _operator(),
            concept_type=_RUNNING,
            modality=Modality.OBSERVED,
            site=_SITE_A,
            requested_fields=("name", "confidence"),
        )
        assert decision.allowed
        assert not decision.is_redacted


@dataclass(frozen=True, slots=True)
class _Row:
    """``AssertionRow`` の最小形（``visible_rows`` が見る属性だけ）。"""

    predicate: str
    object_: str
    modality: Modality
    graph: str
    subject: str = "https://sbp.example/id/pump/P-101"
    assertion_id: str = ""
    confidence: float = 0.9

    def __post_init__(self) -> None:
        if not self.assertion_id:
            object.__setattr__(self, "assertion_id", str(uuid4()))


def _graph(modality: Modality, site: str) -> str:
    return f"urn:sbp:g:{modality.value}:{site}:0.5.0"


class TestSiteOfGraph:
    """グラフ名から拠点を取り出す。"""

    def test_main_graph(self) -> None:
        """主信念のグラフ名から拠点が取れる。"""
        assert site_of_graph(_graph(Modality.OBSERVED, _SITE_A)) == _SITE_A

    def test_scenario_graph_has_no_site(self) -> None:
        """シナリオは拠点を持たない → ``None``（呼び出し側が拒否する）。"""
        assert site_of_graph("urn:sbp:scenario:what-if-1") is None

    def test_unknown_graph_shape_returns_none(self) -> None:
        """規約から外れたグラフ名は ``None``。

        「拠点が判らない＝誰でも見てよい」にしない。規約が変わった日に全拠点へ漏れる。
        """
        assert site_of_graph("http://example.org/some/graph") is None


class TestVisibleRows:
    """行ごとの絞り込み。"""

    def test_keeps_only_permitted_rows(self) -> None:
        """許可された拠点・様相・概念の行だけ残る。"""
        rows = [
            _Row(_LOCATED, _RUNNING, Modality.OBSERVED, _graph(Modality.OBSERVED, _SITE_A)),
            _Row(_LOCATED, _RUNNING, Modality.OBSERVED, _graph(Modality.OBSERVED, _SITE_B)),
            _Row(_LOCATED, _LOCATED, Modality.OBSERVED, _graph(Modality.OBSERVED, _SITE_A)),
            _Row(_LOCATED, _RUNNING, Modality.PLANNED, _graph(Modality.PLANNED, _SITE_A)),
        ]
        kept = visible_rows(_operator(), rows)
        assert len(kept) == 1
        assert kept[0].graph == _graph(Modality.OBSERVED, _SITE_A)

    def test_concept_axis_matches_predicate_or_object(self) -> None:
        """概念型は述語でも目的語でも照合する。

        フルーエントは目的語に型として現れ、関係は述語に現れる。片方だけだと許可が書けない。
        """
        by_predicate = _Row(_RUNNING, "true", Modality.OBSERVED, _graph(Modality.OBSERVED, _SITE_A))
        assert visible_rows(_operator(), [by_predicate])

    def test_scenario_rows_are_dropped(self) -> None:
        """シナリオグラフの行は拠点が判らないので落ちる（既定は拒否）。"""
        row = _Row(_LOCATED, _RUNNING, Modality.HYPOTHETICAL, "urn:sbp:scenario:s1")
        assert visible_rows(_operator(), [row]) == ()

    def test_privacy_callback_drives_redaction_decision(self) -> None:
        """機密度を行ごとに決められる（人の行だけ別扱いにできる）。"""
        rows = [_Row(_LOCATED, _RUNNING, Modality.OBSERVED, _graph(Modality.OBSERVED, _SITE_A))]
        kept = visible_rows(_operator(), rows, privacy_of=lambda _row: PrivacyClass.PERSONAL)
        # 遮蔽対象のフィールドを要求していないので行自体は残る
        assert len(kept) == 1

    def test_empty_input_is_empty_output(self) -> None:
        """空は空（絞り込みが例外を投げない）。"""
        assert visible_rows(_operator(), []) == ()


class TestPrincipalValidation:
    """要求者の検証。"""

    def test_subject_is_required(self) -> None:
        """主体の IRI が無い要求者は作れない（来歴に残せない）。"""
        with pytest.raises(AuthorizationError, match="subject IRI"):
            Principal(subject="")

    def test_decision_carries_a_reason(self) -> None:
        """許可でも理由が入る（監査ログに「なぜ通したか」を残す）。"""
        decision = authorize(
            _operator(), concept_type=_RUNNING, modality=Modality.OBSERVED, site=_SITE_A
        )
        assert decision.reason
        assert "許可されている" in decision.reason
