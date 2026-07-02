"""C2 スキルサーバ・故障注入のテスト。"""

from pathlib import Path

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.skills.server import SkillRequest, SkillServer

WORLD = repo_root() / "configs" / "world" / "t3_capability.yaml"


def make_server(seed: int = 7) -> tuple[SkillServer, WorldConfig]:
    world = load_config(Path(WORLD), WorldConfig)
    return SkillServer(world, SeedTree(seed).child("skills").rng()), world


def request(robot: str, barcode: str, pos=(0.0, 1.2, 0.4)) -> SkillRequest:
    return SkillRequest(
        robot_id=robot,
        skill="pick_and_place",
        target_barcode=barcode,
        target_position=pos,
        dest_zone="dock",
    )


def test_overload_fails_mostly() -> None:
    server, _ = make_server()
    # b5 = 5.5kg > arm_light の 2.0kg
    outcomes = [server.execute(request("arm_light", "BC-305")) for _ in range(100)]
    rate = sum(o.success for o in outcomes) / 100
    assert rate < 0.15
    assert any(o.failure_mode == "overload" for o in outcomes)


def test_material_penalty_statistical() -> None:
    server, _ = make_server()
    # 反射素材: arm_heavy 0.35 vs arm_light 0.85（大数で順序が立つ）
    n = 300
    heavy = sum(server.execute(request("arm_heavy", "BC-307")).success for _ in range(n)) / n
    light = sum(server.execute(request("arm_light", "BC-307")).success for _ in range(n)) / n
    assert abs(heavy - 0.35) < 0.1
    assert abs(light - 0.85) < 0.1


def test_out_of_reach_deterministic() -> None:
    server, _ = make_server()
    outcome = server.execute(request("arm_light", "BC-301", pos=(10.0, 10.0, 0.0)))
    assert not outcome.success
    assert outcome.failure_mode == "out_of_reach"


def test_true_rates_match_profile() -> None:
    server, world = make_server()
    assert server.true_success_rate("arm_light", "BC-305") == 0.05  # overload
    assert server.true_success_rate("arm_heavy", "BC-305") == 0.90  # base
    assert server.true_success_rate("arm_heavy", "BC-307") == 0.35  # reflective


def test_deterministic_given_seed() -> None:
    s1, _ = make_server(11)
    s2, _ = make_server(11)
    o1 = [s1.execute(request("arm_heavy", "BC-307")).success for _ in range(20)]
    o2 = [s2.execute(request("arm_heavy", "BC-307")).success for _ in range(20)]
    assert o1 == o2
