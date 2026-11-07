"""再接地の実行（M9-01、ADR-0031）。

``sbp.knowledge.regrounding`` は計画（``plan``）と差分レポート（``summarise``）だけを持ち、
実行する者がいなかった（所見 110）。ここが実行である: **潜在 → プローブ → 事象昇格 → 信念更新**を、
運用の経路（bridge-worker と belief-worker）と同じ部品で最初から再生し、いまの信念と比べて
差分を出し、``apply`` のときだけ書く。

- **既定は書かない。** ``apply=False`` は差分レポートだけを返す（dry-run）。再接地は信念を
  差し替える操作なので、先に差分を人が見る（runbook の手順 5）。
- **旧文は ``tx_to`` を閉じるだけで消さない**（不変条件 2）。新文は ``valid_from`` を過去のまま、
  ``tx_from`` を実行時刻にして足す。``as_of`` で旧信念が、新しい ``tx`` で新信念が引ける（G7）。
- **ライブと同じ区切りで再生する。** bridge-worker は潜在を ``batch_size`` ずつ読み、バッチごとに
  変化点を探す。再生も同じ区切りで昇格する——1 本の系列として昇格すると、同じプローブでも
  バッチの境目で結果が変わり、差分に偽の変化が出る。最後の 1 点だけのバッチはライブでも
  処理されない（変化点は区間を見る）ので再生も飛ばす。
- **再生は実体の最初の潜在から始める**（対象期間より前から）。フィルタの状態（直前の事後と
  滞在時間）を持ち越すため。書くのは対象期間 ``[start, end)`` の文だけ。
- 比べる相手は ``belief-worker`` の来歴の文だけ（人の訂正や他の来歴を閉じない）。
- 再実行しても同じ（変わらない文は触らない。中断後に新旧が両方開いていても、一致するほうを
  残して残りを閉じる）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from sbp.bridge.lifting import fluent_local_name, lift_events
from sbp.core.errors import RegroundingError
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.knowledge.regrounding import DiffEntry
from sbp.knowledge.regrounding import summarise as summarise_regrounding

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.core.assertion import Assertion
    from sbp.core.config import Settings
    from sbp.core.events import EventEnvelope
    from sbp.knowledge.assertions import AssertionRow, AssertionStore
    from sbp.knowledge.regrounding import RegroundingPlan, RegroundingReport
    from sbp.reasoning.ingest import BeliefIngest, IngestOutcome

    Probes = Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]]
    """潜在の行列 → フルーエント別の較正済み確率（``ProbeSet`` がこの形）。"""

    Series = tuple[Sequence[datetime], NDArray[np.float64]]
    """``(時刻の列, 潜在の行列)``。"""

__all__ = ["RegroundingOutcome", "reground", "replay_entity"]

log = get_logger(__name__)

_SAME = 1e-9
"""この差以下の確信度は同じ文とみなす（浮動小数の再生で出る丸め）。"""


@dataclass(frozen=True, slots=True)
class RegroundingOutcome:
    """再接地の結果。

    Attributes:
        report: 差分レポート（閾値を超えた差分だけを載せる）。
        added: 足した（``apply`` でなければ足すはずだった）文。
        closed: ``tx_to`` を閉じた（閉じるはずだった）旧文。
        applied: 書いたか（偽なら dry-run）。
    """

    report: RegroundingReport
    added: tuple[Assertion, ...]
    closed: tuple[AssertionRow, ...]
    applied: bool


def replay_entity(
    *,
    entity: str,
    series: Series,
    probes: Probes,
    probe_ids: Mapping[str, str],
    wm_version: str,
    rules: object,
    settings: Settings,
    claims: Sequence[EventEnvelope],
    ingest: BeliefIngest,
) -> list[IngestOutcome]:
    """1 実体の信念を、潜在の最初から再生する。

    Args:
        entity: 実体 IRI。
        series: ``(時刻, 潜在)``。時刻の昇順。
        probes: 新しい版のプローブ（潜在の行列 → フルーエント別の較正済み確率。``ProbeSet``）。
        probe_ids: フルーエントのローカル名 → プローブ IRI（イベントの証拠に残る）。
        wm_version: WM 版 IRI。
        rules: 事象計算の規則（``load_event_calculus`` の結果）。
        settings: 設定（バッチの大きさ・接地層の閾値・拠点）。
        claims: 業務記録の主張イベント（この実体のもの以外は無視する）。
        ingest: 信念更新の入口（再生専用の新しいもの）。

    Returns:
        信念を更新した結果（入れなかったイベントは含まない）。
    """
    timestamps, latents = series
    batch = settings.reasoning.batch_size
    events: list[EventEnvelope] = []
    for start in range(0, len(timestamps), batch):
        chunk_times = list(timestamps[start : start + batch])
        if len(chunk_times) < 2:
            break  # ライブでも 1 点では変化点を探さない
        outputs = probes(np.asarray(latents[start : start + batch], dtype=np.float64))
        lifted = lift_events(
            subject=entity,
            probe_outputs=outputs,
            timestamps=chunk_times,
            rules=rules,  # type: ignore[arg-type]
            settings=settings.bridge,
            probe_ids={name: probe_ids[name] for name in outputs},
            wm_version=wm_version,
        )
        events.extend(
            event.to_envelope(
                site_id=settings.site_id,
                event_type_iri=f"sbp-core:PhysicalEvent/ent:{fluent_local_name(event.fluent)}",
                ontology_version=ingest.ontology_version,
            )
            for event in lifted
        )
    events.extend(claim for claim in claims if str(claim.subject) == entity)
    events.sort(key=lambda event: event.tx_time)
    outcomes = [ingest.handle(event) for event in events]
    return [outcome for outcome in outcomes if outcome.update is not None]


def _in_scope(at: datetime, plan: RegroundingPlan) -> bool:
    end = plan.interval.end
    return plan.interval.start <= at and (end is None or at < end)


def reground(
    plan: RegroundingPlan,
    *,
    store: AssertionStore,
    series_by_entity: Mapping[str, Series],
    probes: Probes,
    probe_ids: Mapping[str, str],
    wm_version: str,
    rules: object,
    settings: Settings,
    claims: Sequence[EventEnvelope],
    ingest_factory: Callable[[], BeliefIngest],
    apply: bool,
    at: datetime | None = None,
) -> RegroundingOutcome:
    """計画の範囲を再生して差分を出し、``apply`` なら書く。

    Args:
        plan: 再接地の計画（対象の実体・フルーエント・期間）。
        store: アサーションストア。
        series_by_entity: 実体 IRI → ``(時刻, 潜在)``。計画の実体を全て含むこと。
        probes: 新しい版のプローブ（``ProbeSet``）。
        probe_ids: フルーエントのローカル名 → プローブ IRI。
        wm_version: WM 版 IRI。
        rules: 事象計算の規則。
        settings: 設定。
        claims: 業務記録の主張イベント。
        ingest_factory: 再生専用の信念更新の入口を作る関数（呼ぶたびに新しいもの）。
        apply: 真なら書く。偽なら差分だけ。
        at: 実行時刻（新文の ``tx_from`` と旧文を閉じる時刻。既定は現在）。

    Returns:
        結果。

    Raises:
        RegroundingError: 計画の実体の潜在が渡されていない場合。
    """
    started = at or now()
    missing = [entity for entity in plan.entities if entity not in series_by_entity]
    if missing:
        raise RegroundingError(
            f"no latents were given for {missing}; regrounding them would close their beliefs "
            "without replacing them"
        )
    diffs: list[DiffEntry] = []
    added: list[Assertion] = []
    closed: list[AssertionRow] = []
    reassessed = 0
    for entity in plan.entities:
        ingest = ingest_factory()
        outcomes = replay_entity(
            entity=entity,
            series=series_by_entity[entity],
            probes=probes,
            probe_ids=probe_ids,
            wm_version=wm_version,
            rules=rules,
            settings=settings,
            claims=claims,
            ingest=ingest,
        )
        fresh = {
            outcome.assertion.valid_from: outcome.assertion
            for outcome in outcomes
            if outcome.assertion is not None
            and outcome.assertion.predicate in plan.fluents
            and _in_scope(outcome.assertion.valid_from, plan)
        }
        for predicate in plan.fluents:
            old = _old_rows(store, entity, predicate, plan, ingest.provenance)
            mine = {when: a for when, a in fresh.items() if a.predicate == predicate}
            more_added, more_closed, more_diffs = _compare(
                entity=entity,
                predicate=predicate,
                old=old,
                fresh=mine,
                plan=plan,
                at=started,
                provenance=ingest.provenance,
            )
            added += more_added
            closed += more_closed
            diffs += more_diffs
            reassessed += len(mine) + sum(1 for d in more_diffs if d.is_gone)
    if apply:
        for assertion in added:
            store.assert_(assertion)
        for row in closed:
            store.retract(row.assertion_id, at=started)
    finished = now() if at is None else started
    report = summarise_regrounding(
        plan,
        diffs=diffs,
        reassessed=max(reassessed, len(diffs)),
        started_at=started,
        finished_at=max(finished, started),
        closed_assertions=len(closed) if apply else 0,
    )
    log.info(
        "reasoning.regrounding_run",
        plan=str(plan.plan_id),
        applied=apply,
        added=len(added),
        closed=len(closed),
        diffs=len(diffs),
    )
    return RegroundingOutcome(
        report=report, added=tuple(added), closed=tuple(closed), applied=apply
    )


def _old_rows(
    store: AssertionStore, entity: str, predicate: str, plan: RegroundingPlan, provenance: str
) -> dict[datetime, list[AssertionRow]]:
    """いまの信念（belief-worker の来歴の、開いている推定の文）を valid_from ごとに束ねる。"""
    rows = store.query(subject=entity, predicate=predicate, modality=Modality.INFERRED)
    grouped: dict[datetime, list[AssertionRow]] = {}
    for row in rows:
        if row.provenance.startswith(provenance) and _in_scope(row.valid_from, plan):
            grouped.setdefault(row.valid_from, []).append(row)
    return grouped


def _compare(
    *,
    entity: str,
    predicate: str,
    old: Mapping[datetime, list[AssertionRow]],
    fresh: Mapping[datetime, Assertion],
    plan: RegroundingPlan,
    at: datetime,
    provenance: str,
) -> tuple[list[Assertion], list[AssertionRow], list[DiffEntry]]:
    """旧文と再生した文を valid_from で突き合わせる。"""
    added: list[Assertion] = []
    closed: list[AssertionRow] = []
    diffs: list[DiffEntry] = []
    for when in sorted(set(old) | set(fresh)):
        rows = old.get(when, [])
        new = fresh.get(when)
        new_p = None if new is None else _confidence(new)
        if new is None:
            closed.extend(rows)
            diffs.append(
                DiffEntry(
                    entity, predicate, when, old_confidence=rows[0].confidence, new_confidence=None
                )
            )
            continue
        keep = min(rows, key=lambda r: abs(r.confidence - (new_p or 0.0)), default=None)
        if keep is not None and abs(keep.confidence - (new_p or 0.0)) <= _SAME:
            closed.extend(row for row in rows if row is not keep)  # 中断後の重複
            continue
        closed.extend(rows)
        added.append(
            new.model_copy(
                update={"tx_from": at, "provenance": f"{provenance}/regrounding/{plan.plan_id}"}
            )
        )
        diffs.append(
            DiffEntry(
                entity,
                predicate,
                when,
                old_confidence=None if not rows else rows[0].confidence,
                new_confidence=new_p,
            )
        )
    return added, closed, diffs


def _confidence(assertion: Assertion) -> float:
    """真偽の信念の確率。"""
    return float(assertion.belief.p)  # type: ignore[union-attr]
