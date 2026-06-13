"""S2 汚染推移閉包オラクルの単体テスト（ADR-015、手構築フィクスチャ）。ORコア非依存。"""

from orx.common.schemas import ContactEvent, ResetEvent
from orx.oracle.scenarios.s2 import contamination_closure, grasp_allowed

# 源: 箱A はピーナッツ含有（intrinsic）。グリッパ/トレイ/箱B は初期清浄。
INTRINSIC = {"boxA": {"peanut"}, "gripper": set(), "trayT": set(), "boxB": set(), "gloveX": set()}


def test_direct_contact_transfers() -> None:
    contacts = [ContactEvent(a="gripper", b="boxA", sim_time=1.0)]
    st = contamination_closure(INTRINSIC, contacts, [], at_time=2.0)
    assert st.carries("gripper", "peanut")
    assert st.carries("boxA", "peanut")  # 源は当然保持


def test_two_hop_transitive_closure() -> None:
    # gripper→boxA（汚染）, gripper→trayT, trayT→boxB ⇒ boxB が peanut を保持
    contacts = [
        ContactEvent(a="gripper", b="boxA", sim_time=1.0),
        ContactEvent(a="gripper", b="trayT", sim_time=2.0),
        ContactEvent(a="trayT", b="boxB", sim_time=3.0),
    ]
    st = contamination_closure(INTRINSIC, contacts, [], at_time=4.0)
    assert st.carries("boxB", "peanut")  # 2ホップ伝播（B0/B1には解けない）


def test_time_order_forward_only() -> None:
    # boxB への伝播は t=3。t=2.5 時点では未汚染。
    contacts = [
        ContactEvent(a="gripper", b="boxA", sim_time=1.0),
        ContactEvent(a="gripper", b="boxB", sim_time=3.0),
    ]
    assert not contamination_closure(INTRINSIC, contacts, [], at_time=2.5).carries("boxB", "peanut")
    assert contamination_closure(INTRINSIC, contacts, [], at_time=3.5).carries("boxB", "peanut")


def test_cleaning_resets_acquired_not_intrinsic() -> None:
    contacts = [ContactEvent(a="gripper", b="boxA", sim_time=1.0)]
    cleanings = [ResetEvent(entity="gripper", sim_time=2.0)]
    st = contamination_closure(INTRINSIC, contacts, cleanings, at_time=3.0)
    assert not st.carries("gripper", "peanut")  # 洗浄で acquired リセット
    assert st.carries("boxA", "peanut")  # 源は洗っても源（intrinsic）


def test_recontamination_after_cleaning() -> None:
    contacts = [
        ContactEvent(a="gripper", b="boxA", sim_time=1.0),
        ContactEvent(a="gripper", b="boxA", sim_time=3.0),  # 洗浄後に再接触
    ]
    cleanings = [ResetEvent(entity="gripper", sim_time=2.0)]
    assert contamination_closure(INTRINSIC, contacts, cleanings, at_time=3.5).carries(
        "gripper", "peanut"
    )


def test_same_tick_clean_after_contact() -> None:
    # 同時刻は contact→clean の順 ⇒ 触れてから洗った＝清浄
    contacts = [ContactEvent(a="gripper", b="boxA", sim_time=1.0)]
    cleanings = [ResetEvent(entity="gripper", sim_time=1.0)]
    assert not contamination_closure(INTRINSIC, contacts, cleanings, at_time=1.0).carries(
        "gripper", "peanut"
    )


def test_grasp_allowed_check() -> None:
    contacts = [ContactEvent(a="gripper", b="boxA", sim_time=1.0)]
    st = contamination_closure(INTRINSIC, contacts, [], at_time=2.0)
    # peanut-free 製品の把持は禁止（偽陰性を出すとアレルゲン事故）
    assert not grasp_allowed(st, "gripper", allergen_free_of={"peanut"})
    # gluten-free 製品なら（peanutしか持たないので）許可
    assert grasp_allowed(st, "gripper", allergen_free_of={"gluten"})
    # 清浄なグローブは許可
    assert grasp_allowed(st, "gloveX", allergen_free_of={"peanut"})
