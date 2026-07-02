"""T3 能力考慮計画 / T6 エピソード較正スイート（H3）。

- 能力台帳（世界グラフ上の orx-cap: 主張）が経験で較正され、割当品質を上げるか。
- 計画器は台帳の推定のみを見る。真の注入率は採点・較正誤差の計算にのみ使う。
"""

from __future__ import annotations

import numpy as np

from orx.common import iri
from orx.common.config import RobotConfig, WorldConfig
from orx.common.schemas import Claim, StrictModel, Term, Vec3
from orx.common.seeding import SeedTree, deterministic_id
from orx.kg.world_graph import WorldGraph
from orx.skills.server import SkillOutcome, SkillRequest, SkillServer


def capability_iri(robot_id: str, skill: str, material: str) -> str:
    return iri.entity("capability", f"{robot_id}-{skill}-{material}")


class CapabilityLedger:
    """能力台帳: 世界グラフ上の主張＋計画用の高速ミラー。

    宣言（能力契約）を事前分布、実行エピソードを観測としてベイズ的に推定する。
    """

    PRIOR_WEIGHT = 2.0

    def __init__(self, graph: WorldGraph, seeds: SeedTree) -> None:
        self.graph = graph
        self._rng = seeds.child("ledger").rng()
        self._declarations: dict[str, RobotConfig] = {}
        self._stats: dict[tuple[str, str], tuple[int, int]] = {}  # (robot,material)->(n,s)

    def _claim(self, subject: str, predicate: str, obj: Term, agent: str, t: float) -> None:
        self.graph.assert_claim(
            Claim(
                claim_id=deterministic_id(self._rng),
                subject=subject,
                predicate=predicate,
                object=obj,
                asserted_by=agent,
                confidence=1.0,
                observed_at=t,
                valid_until=None,
            )
        )

    def declare(self, robot: RobotConfig, t: float = 0.0) -> None:
        if robot.capability is None:
            return
        self._declarations[robot.name] = robot
        agent = iri.entity("agent", f"skills-{robot.name}")
        for skill in robot.capability.skills:
            cap = capability_iri(robot.name, skill, "any")
            self._claim(cap, iri.RDF_TYPE, Term(kind="iri", value=iri.cap("Capability")), agent, t)
            self._claim(
                cap,
                iri.cap("forRobot"),
                Term(kind="iri", value=iri.entity("robot", robot.name)),
                agent,
                t,
            )
            self._claim(cap, iri.cap("skillType"), Term(kind="literal", value=skill), agent, t)
            self._claim(
                cap,
                iri.cap("declaredPayloadKg"),
                Term(
                    kind="literal",
                    value=repr(robot.capability.declared_payload_kg),
                    datatype=iri.XSD_DOUBLE,
                ),
                agent,
                t,
            )
            self._claim(
                cap,
                iri.cap("declaredReachM"),
                Term(
                    kind="literal",
                    value=repr(robot.capability.declared_reach_m),
                    datatype=iri.XSD_DOUBLE,
                ),
                agent,
                t,
            )

    def record_outcome(self, robot_id: str, material: str, success: bool, t: float) -> None:
        n, s = self._stats.get((robot_id, material), (0, 0))
        n, s = n + 1, s + (1 if success else 0)
        self._stats[(robot_id, material)] = (n, s)
        agent = iri.entity("agent", f"skills-{robot_id}")
        cap = capability_iri(robot_id, "pick_and_place", material)
        xsd_int = "http://www.w3.org/2001/XMLSchema#integer"
        self._claim(cap, iri.cap("materialTag"), Term(kind="literal", value=material), agent, t)
        self._claim(
            cap, iri.cap("trials"), Term(kind="literal", value=str(n), datatype=xsd_int), agent, t
        )
        self._claim(
            cap,
            iri.cap("successes"),
            Term(kind="literal", value=str(s), datatype=xsd_int),
            agent,
            t,
        )

    def estimate(self, robot_id: str, material: str) -> float:
        """宣言を事前分布としたベータ事後平均。"""
        robot = self._declarations.get(robot_id)
        prior = robot.capability.prior_success if robot and robot.capability else 0.5
        n, s = self._stats.get((robot_id, material), (0, 0))
        return (s + prior * self.PRIOR_WEIGHT) / (n + self.PRIOR_WEIGHT)

    def feasible(self, robot_id: str, weight_kg: float, distance_m: float) -> bool:
        robot = self._declarations.get(robot_id)
        if robot is None or robot.capability is None:
            return False
        return (
            weight_kg <= robot.capability.declared_payload_kg
            and distance_m <= robot.capability.declared_reach_m
        )


class AllocationTask(StrictModel):
    """『{weight}kg・{material} の箱 {barcode} を {dest} へ』という作業指示。"""

    barcode: str
    weight_kg: float
    material: str
    position: Vec3
    dest_zone: str


def make_tasks(
    world: WorldConfig, graph: WorldGraph, dest_zone: str, at_time: float
) -> list[AllocationTask]:
    """作業指示を生成。位置は世界グラフ（計画の窓）から引く。"""
    tasks: list[AllocationTask] = []
    snapshot = graph.snapshot(at_time=at_time)
    barcode_to_entity: dict[str, str] = {}
    for t in snapshot.triples:
        if t.predicate == iri.upper("hasIdentifier"):
            barcode_to_entity[t.object.strip('"')] = t.subject
    for box in world.boxes:
        if box.barcode is None:
            continue
        entity = barcode_to_entity.get(box.barcode)
        if entity is None or entity not in snapshot.positions:
            continue  # グラフが知らない箱は計画対象外（ログで把握）
        tasks.append(
            AllocationTask(
                barcode=box.barcode,
                weight_kg=box.weight_kg,
                material=box.material,
                position=snapshot.positions[entity],
                dest_zone=dest_zone,
            )
        )
    return tasks


def allocate_by_capability(
    ledger: CapabilityLedger,
    robots: list[RobotConfig],
    task: AllocationTask,
    rng: np.random.Generator,
    epsilon: float = 0.0,
) -> str | None:
    """能力契約（実現可能性）＋台帳推定（成功率）で割当。ε-greedyで探索。"""
    candidates = []
    for robot in robots:
        if robot.capability is None:
            continue
        d = float(np.linalg.norm(np.array(robot.camera.pos) - np.array(task.position)))
        if ledger.feasible(robot.name, task.weight_kg, d):
            candidates.append(robot.name)
    if not candidates:
        return None
    if epsilon > 0 and rng.random() < epsilon:
        return candidates[int(rng.integers(0, len(candidates)))]
    return max(candidates, key=lambda r: (ledger.estimate(r, task.material), r))


class RoundRobin:
    """ベースライン: 能力を見ない巡回割当。"""

    def __init__(self, robots: list[RobotConfig]) -> None:
        self._names = [r.name for r in robots if r.skill_truth is not None]
        self._i = 0

    def next(self) -> str:
        name = self._names[self._i % len(self._names)]
        self._i += 1
        return name


def best_true_robot(server: SkillServer, robots: list[RobotConfig], barcode: str) -> float:
    """真の最良成功率（採点専用）。"""
    return max(server.true_success_rate(r.name, barcode) for r in robots)


def allocation_correct(
    server: SkillServer,
    robots: list[RobotConfig],
    barcode: str,
    chosen: str | None,
    tolerance: float = 0.05,
) -> bool:
    if chosen is None:
        return False
    best = best_true_robot(server, robots, barcode)
    return server.true_success_rate(chosen, barcode) >= best - tolerance


class T6EpisodeMetrics(StrictModel):
    episode: int
    brier: float  # 生Brier: (推定 - 成否)^2 平均。方策変化に伴う結果分散を含む
    brier_reliability: float  # Brier分解の信頼性項: (推定 - 真率)^2 平均（較正の本体）
    calibration_mae: float  # 訪問セルの |推定 - 真率| 平均
    allocation_accuracy: float


def run_learning_episodes(
    world: WorldConfig,
    graph: WorldGraph,
    server: SkillServer,
    ledger: CapabilityLedger,
    seeds: SeedTree,
    n_episodes: int,
    epsilon: float,
    dest_zone: str,
    graph_time: float,
) -> tuple[list[T6EpisodeMetrics], list[tuple[bool, bool]], int]:
    """学習ループ（T6）＋ capability vs round-robin の対割当（T3）。

    Returns: (エピソード毎の較正指標, [(cap正答, rr正答)] 全タスク, 再計画回数)
    """
    robots = [r for r in world.robots if r.skill_truth is not None]
    for robot in robots:
        ledger.declare(robot)
    tasks = make_tasks(world, graph, dest_zone, graph_time)
    if not tasks:
        raise ValueError("計画対象タスクが空です（グラフに識別子付き個体が無い）")
    explore_rng = seeds.child("explore").rng()
    rr = RoundRobin(robots)
    metrics: list[T6EpisodeMetrics] = []
    paired: list[tuple[bool, bool]] = []
    replans = 0
    sim_t = 0.0
    for episode in range(1, n_episodes + 1):
        briers: list[float] = []
        correct_count = 0
        for task in tasks:
            sim_t += 1.0
            chosen = allocate_by_capability(ledger, robots, task, explore_rng, epsilon)
            rr_choice = rr.next()
            cap_ok = allocation_correct(server, robots, task.barcode, chosen)
            rr_ok = allocation_correct(server, robots, task.barcode, rr_choice)
            paired.append((cap_ok, rr_ok))
            correct_count += 1 if cap_ok else 0
            if chosen is None:
                continue
            estimate = ledger.estimate(chosen, task.material)
            outcome: SkillOutcome = server.execute(
                SkillRequest(
                    robot_id=chosen,
                    skill="pick_and_place",
                    target_barcode=task.barcode,
                    target_position=task.position,
                    dest_zone=task.dest_zone,
                ),
                sim_time=sim_t,
            )
            briers.append((estimate - (1.0 if outcome.success else 0.0)) ** 2)
            ledger.record_outcome(chosen, task.material, outcome.success, sim_t)
            if not outcome.success:
                # 再計画: 次善のロボットで1回だけ再試行
                others = [r for r in robots if r.name != chosen]
                retry = allocate_by_capability(ledger, others, task, explore_rng, 0.0)
                if retry is not None:
                    replans += 1
                    retry_est = ledger.estimate(retry, task.material)
                    retry_outcome = server.execute(
                        SkillRequest(
                            robot_id=retry,
                            skill="pick_and_place",
                            target_barcode=task.barcode,
                            target_position=task.position,
                            dest_zone=task.dest_zone,
                        ),
                        sim_time=sim_t + 0.5,
                    )
                    briers.append((retry_est - (1.0 if retry_outcome.success else 0.0)) ** 2)
                    ledger.record_outcome(retry, task.material, retry_outcome.success, sim_t + 0.5)
        # 較正誤差（Brier信頼性項）: 訪問済みセルの推定 vs 真率
        errors: list[float] = []
        sq_errors: list[float] = []
        for (robot_id, material), (n, _) in ledger._stats.items():
            if n == 0:
                continue
            barcode = next((t.barcode for t in tasks if t.material == material), None)
            if barcode is None:
                continue
            true_rate = server.true_success_rate(robot_id, barcode)
            diff = ledger.estimate(robot_id, material) - true_rate
            errors.append(abs(diff))
            sq_errors.append(diff * diff)
        metrics.append(
            T6EpisodeMetrics(
                episode=episode,
                brier=sum(briers) / len(briers) if briers else 0.0,
                brier_reliability=sum(sq_errors) / len(sq_errors) if sq_errors else 0.0,
                calibration_mae=sum(errors) / len(errors) if errors else 0.0,
                allocation_accuracy=correct_count / len(tasks),
            )
        )
    return metrics, paired, replans
