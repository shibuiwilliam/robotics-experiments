"""C9 — 実行記録の書込/読出（JSONL ストリーム＋マニフェスト）。

全実験は記録を経由する（CLAUDE.md §3.6）。書込は追記専用で、途中失敗しても
既存ストリームを壊さない（metrics.json が無い run は未完了と判定できる）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import IO, TypeVar

from pydantic import BaseModel

from orx.common.config import RunConfig
from orx.common.schemas import (
    ActionReceiptRecord,
    ActionRequestRecord,
    AnchorObservation,
    AnchorRecord,
    Claim,
    CustodyRecord,
    FidelityReport,
    PerceptionEvent,
    RawObservation,
    RunManifest,
    StateSnapshot,
    TruthState,
)

M = TypeVar("M", bound=BaseModel)

STREAMS = {
    "observations": "observations.jsonl",
    "events": "perception_events.jsonl",
    "claims": "claims.jsonl",
    "anchor_records": "anchor_records.jsonl",
    "anchor_observations": "anchor_observations.jsonl",
    "truth": "truth.jsonl",
    "world_snapshots": "world_snapshots.jsonl",
    # S8 / キネティック層の監査ストリーム（PROJECT.md §5.2-6, IMPROVEMENT.md §3.5）
    "actions": "actions.jsonl",
    "action_receipts": "action_receipts.jsonl",
    "custody": "custody.jsonl",
}
MANIFEST = "manifest.json"
CONFIG = "config.json"
METRICS = "metrics.json"


def next_available_run_dir(runs_root: Path, base_run_id: str) -> Path:
    """既存runを壊さないよう、空いているrun_idのディレクトリを返す。"""
    candidate = runs_root / base_run_id
    n = 1
    while candidate.exists():
        n += 1
        candidate = runs_root / f"{base_run_id}-{n}"
    return candidate


class RunWriter:
    """1 run の追記専用ライタ。"""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, IO[str]] = {}

    def _append(self, stream: str, model: BaseModel) -> None:
        handle = self._handles.get(stream)
        if handle is None:
            handle = (self.run_dir / STREAMS[stream]).open("a", encoding="utf-8")
            self._handles[stream] = handle
        handle.write(model.model_dump_json() + "\n")

    def append_observation(self, obs: RawObservation) -> None:
        self._append("observations", obs)

    def append_event(self, event: PerceptionEvent) -> None:
        self._append("events", event)

    def append_claim(self, claim: Claim) -> None:
        self._append("claims", claim)

    def append_anchor_record(self, record: AnchorRecord) -> None:
        self._append("anchor_records", record)

    def append_anchor_observation(self, obs: AnchorObservation) -> None:
        self._append("anchor_observations", obs)

    def append_truth(self, state: TruthState) -> None:
        self._append("truth", state)

    def append_world_snapshot(self, snap: StateSnapshot) -> None:
        self._append("world_snapshots", snap)

    def append_action(self, action: ActionRequestRecord) -> None:
        self._append("actions", action)

    def append_action_receipt(self, receipt: ActionReceiptRecord) -> None:
        self._append("action_receipts", receipt)

    def append_custody(self, step: CustodyRecord) -> None:
        self._append("custody", step)

    def write_manifest(self, manifest: RunManifest) -> None:
        (self.run_dir / MANIFEST).write_text(
            manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def write_config(self, config: RunConfig) -> None:
        (self.run_dir / CONFIG).write_text(
            config.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def write_metrics(self, report: FidelityReport) -> None:
        (self.run_dir / METRICS).write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def __enter__(self) -> RunWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RunReader:
    """1 run の型付き読出。"""

    def __init__(self, run_dir: Path) -> None:
        if not run_dir.exists():
            raise FileNotFoundError(f"run が見つかりません: {run_dir}")
        self.run_dir = run_dir

    def _iter(self, stream: str, model: type[M]) -> Iterator[M]:
        path = self.run_dir / STREAMS[stream]
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield model.model_validate_json(line)

    def manifest(self) -> RunManifest:
        return RunManifest.model_validate_json(
            (self.run_dir / MANIFEST).read_text(encoding="utf-8")
        )

    def config(self) -> RunConfig:
        return RunConfig.model_validate_json((self.run_dir / CONFIG).read_text(encoding="utf-8"))

    def observations(self) -> Iterator[RawObservation]:
        return self._iter("observations", RawObservation)

    def events(self) -> Iterator[PerceptionEvent]:
        return self._iter("events", PerceptionEvent)

    def claims(self) -> Iterator[Claim]:
        return self._iter("claims", Claim)

    def anchor_observations(self) -> Iterator[AnchorObservation]:
        return self._iter("anchor_observations", AnchorObservation)

    def truth_states(self) -> Iterator[TruthState]:
        return self._iter("truth", TruthState)

    def world_snapshots(self) -> Iterator[StateSnapshot]:
        return self._iter("world_snapshots", StateSnapshot)

    def actions(self) -> Iterator[ActionRequestRecord]:
        return self._iter("actions", ActionRequestRecord)

    def action_receipts(self) -> Iterator[ActionReceiptRecord]:
        return self._iter("action_receipts", ActionReceiptRecord)

    def custody(self) -> Iterator[CustodyRecord]:
        return self._iter("custody", CustodyRecord)

    def is_complete(self) -> bool:
        return (self.run_dir / METRICS).exists()

    def metrics(self) -> FidelityReport:
        if not self.is_complete():
            raise FileNotFoundError(f"metrics.json がありません（未完了run）: {self.run_dir}")
        return FidelityReport.model_validate_json(
            (self.run_dir / METRICS).read_text(encoding="utf-8")
        )

    def metrics_bytes(self) -> bytes:
        return (self.run_dir / METRICS).read_bytes()


def metrics_json(report: FidelityReport) -> str:
    """メトリクスの正準JSON（リプレイ同一性のバイト比較に使う形式）。"""
    return json.dumps(
        json.loads(report.model_dump_json()), sort_keys=True, ensure_ascii=False, indent=2
    )
