"""リフティング（ベンダースキーマ→共通検出）のテスト。"""

import pytest

from orx.common.schemas import RawObservation
from orx.perception.lifting import lift, load_mapping


def make_obs(**payload_overrides: object) -> RawObservation:
    payload: dict = {
        "hdr": {"rid": "arm_a", "ts": 1.5, "n": 2},
        "dets": [
            {"p": {"x": 0.1, "y": 0.2, "z": 0.3}, "bc": "BC-001", "cf": 0.97},
            {"p": {"x": -0.4, "y": 0.5, "z": 0.6}, "bc": None, "cf": 0.91},
        ],
    }
    payload.update(payload_overrides)
    return RawObservation(
        robot_id="arm_a",
        vendor_schema="vendor_arm_a",
        sim_time=1.5,
        seq=3,
        payload=payload,
        oracle_truth_ids=["b1", "b4"],
    )


def test_load_mapping_from_repo() -> None:
    mapping = load_mapping("vendor_arm_a")
    assert mapping.schema_name == "vendor_arm_a"
    assert mapping.position.units == "m"


def test_lift_vendor_a() -> None:
    dets = lift(make_obs(), load_mapping("vendor_arm_a"))
    assert len(dets) == 2
    assert dets[0].position == (0.1, 0.2, 0.3)
    assert dets[0].symbol_id == "BC-001"
    assert dets[0].confidence == 0.97
    assert dets[1].symbol_id is None
    assert dets[0].embedding is None  # 埋め込みはパイプラインの仕事


def test_lift_schema_mismatch_rejected() -> None:
    obs = make_obs().model_copy(update={"vendor_schema": "vendor_other"})
    with pytest.raises(ValueError, match="スキーマ不一致"):
        lift(obs, load_mapping("vendor_arm_a"))


def test_lift_missing_path_actionable() -> None:
    obs = make_obs(dets=[{"wrong": 1}])
    with pytest.raises(KeyError, match="リフティング失敗"):
        lift(obs, load_mapping("vendor_arm_a"))
