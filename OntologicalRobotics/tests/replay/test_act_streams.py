"""K0 — アクション監査ストリーム（actions/action_receipts/custody）の記録→再読込同一性。

リプレイ同一性（P0 完了基準・PROJECT.md §5.2-6）をキネティック層へ拡張する。
"""

from __future__ import annotations

from pathlib import Path

from orx.common.schemas import (
    ActionReceiptRecord,
    ActionRequestRecord,
    CustodyRecord,
)
from orx.replay.io import RunReader, RunWriter


def _records():
    actions = [
        ActionRequestRecord(
            action_id="act-0001",
            robot_id="r1",
            skill="pick_and_place",
            target_barcode="BC-X",
            target_position=(0.0, 0.0, 0.4),
            dest_zone="dock",
            at_time=2.0,
        )
    ]
    receipts = [
        ActionReceiptRecord(
            action_id="act-0001",
            object_id="BC-X",
            status="applied",
            effect_applied=True,
            at_time=2.0,
        ),
        ActionReceiptRecord(
            action_id="act-0002",
            object_id="BC-Y",
            status="rejected",
            reason="forbidden_zone",
            at_time=2.5,
        ),
    ]
    custody = [
        CustodyRecord(object_id="BC-X", zone="dock", step_index=0, by_robot="r1", at_time=2.0),
    ]
    return actions, receipts, custody


def test_action_streams_round_trip(tmp_path: Path) -> None:
    actions, receipts, custody = _records()
    with RunWriter(tmp_path) as writer:
        for a in actions:
            writer.append_action(a)
        for r in receipts:
            writer.append_action_receipt(r)
        for c in custody:
            writer.append_custody(c)

    reader = RunReader(tmp_path)
    assert list(reader.actions()) == actions
    assert list(reader.action_receipts()) == receipts
    assert list(reader.custody()) == custody


def test_missing_streams_yield_empty(tmp_path: Path) -> None:
    """ストリーム未生成（アクションが無いエピソード）でも空イテレータを返す。"""
    reader = RunReader(tmp_path)
    assert list(reader.actions()) == []
    assert list(reader.action_receipts()) == []
    assert list(reader.custody()) == []
