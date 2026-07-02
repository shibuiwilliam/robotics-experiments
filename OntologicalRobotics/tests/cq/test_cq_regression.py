"""CQ回帰テスト — オントロジーの仕様検査（PROJECT.md §6）。

正準フィクスチャ（demo_tiny 12秒記録、stub・オフライン）に対して
tasks/competency_questions/ の全CQを実行し、全問合格を要求する。
語彙変更でCQが答えられなくなればここが落ちる。
"""

import pytest

from orx.common.config import RunConfig, WorldConfig, load_config
from orx.common.paths import repo_root
from orx.exp.cq import load_cqs, run_all_cqs
from orx.exp.episode import record_episode

WORLD_PATH = repo_root() / "configs" / "world" / "demo_tiny.yaml"


@pytest.fixture(scope="module")
def canonical_run(tmp_path_factory: pytest.TempPathFactory):
    runs_root = tmp_path_factory.mktemp("cq_runs")
    world = load_config(WORLD_PATH, WorldConfig)
    config = RunConfig(world=world, duration_s=12.0, root_seed=7)
    run_id, _ = record_episode(config, runs_root)
    return runs_root / run_id


def test_cq_definitions_load() -> None:
    cqs = load_cqs()
    assert len(cqs) >= 4
    assert all(cq.sparql.strip().upper().startswith("PREFIX") for cq in cqs)


def test_all_cqs_pass(canonical_run) -> None:
    results = run_all_cqs(canonical_run)
    failures = [r for r in results if not r.passed]
    detail = "\n".join(f"{r.cq_id}: got={r.got} expected={r.expected}" for r in failures)
    assert not failures, f"CQ回帰失敗:\n{detail}"
