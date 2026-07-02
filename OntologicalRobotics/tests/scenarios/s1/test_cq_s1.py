"""S1 語彙のCQ回帰（SCENARIOS.md §6: 語彙拡張はCQとセット）。

宣言的CQ（期待SPARQL）を S1 世界グラフに対して実行し、oracle 真値と一致を確認する。
"""

import pytest
import yaml

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.exp.suites.s1_lot_recall import runner
from orx.oracle.scenarios.s1 import recall_truth

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s1_lot_recall"
CURRENT = "https://orx.local/id/graph/current"


@pytest.fixture(scope="module")
def episode(tmp_path_factory: pytest.TempPathFactory):
    world = load_config(repo_root() / runner.WORLD, WorldConfig)
    ep = runner.prepare_episode(
        world,
        seed=102,
        runs_root=tmp_path_factory.mktemp("s1cq"),
        duration_s=18.0,
        claim_ttl_s=8.0,
    )
    ep.graph.refresh_current_graph(ep.recall_time)
    return ep


def test_cq_files_exist_and_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in data and "sparql" in data


def test_cq_lot_membership_matches_business(episode) -> None:
    cq = yaml.safe_load((CQ_DIR / "cq_s1_lot_membership.yaml").read_text(encoding="utf-8"))
    sparql = cq["sparql"].replace("{lot}", episode.recall_lot)
    rows = episode.graph.query(sparql)
    got = {r["bc"] for r in rows}
    expected = {bc for bc, lot in episode.lot_members.items() if lot == episode.recall_lot}
    assert got == expected


def test_cq_recall_targets_matches_oracle(episode) -> None:
    cq = yaml.safe_load((CQ_DIR / "cq_s1_recall_targets.yaml").read_text(encoding="utf-8"))
    sparql = cq["sparql"].replace("{recall_lot}", episode.recall_lot)
    rows = episode.graph.query(sparql)
    got = {r["bc"] for r in rows}
    truth = recall_truth(
        episode.truth_states, episode.lot_members, episode.recall_lot, episode.recall_time
    )
    # OR-full は全回収対象を列挙できる（搬送済み個体含む）
    assert got == set(truth.targets)
