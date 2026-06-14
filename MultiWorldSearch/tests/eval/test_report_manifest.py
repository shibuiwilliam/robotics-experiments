"""IMPROVEMENT P10/M15 — run manifests must be sufficient to reproduce."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from mws.eval.report import create_manifest, get_git_state

# Every field CLAUDE.md §9 requires a manifest to carry.
_REQUIRED_FIELDS = (
    "run_id",
    "scenario",
    "timestamp",
    "git_sha",
    "git_dirty",
    "git_untracked_tree",
    "seed",
    "cloud_mode",
    "embedding_space",
    "embedding_dims",
    "embedding_batch_size",
    "model_ids",
    "python_version",
    "dependency_versions",
)


def test_manifest_contains_all_reproducibility_fields() -> None:
    manifest = create_manifest(run_id="r1", scenario="test", seed=7)
    for field in _REQUIRED_FIELDS:
        assert field in manifest, f"manifest missing {field}"
    assert manifest["seed"] == 7
    # Model ids come from their single sources of truth.
    assert manifest["model_ids"]["embedding"] == "gemini-embedding-2"
    assert manifest["model_ids"]["llm"] == "gemini-3.5-flash"
    # Key dependency versions are resolved (installed in this env).
    for dep in ("mujoco", "lancedb", "duckdb", "google-genai"):
        assert manifest["dependency_versions"][dep] not in ("", None)


def _fake_git(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_git_state_clean_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git(monkeypatch, "")
    assert get_git_state() == {"git_dirty": False, "git_untracked_tree": False}


def test_git_state_dirty_tracked_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git(monkeypatch, " M mws/cli.py\n?? notes.txt\n")
    state = get_git_state()
    assert state["git_dirty"] is True
    assert state["git_untracked_tree"] is False


def test_git_state_entirely_untracked_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The M15 incident: the whole MWS tree untracked → the SHA describes
    NONE of the code that ran, and the manifest must say so."""
    _fake_git(monkeypatch, "?? ./\n")
    state = get_git_state()
    assert state["git_dirty"] is True
    assert state["git_untracked_tree"] is True


def test_git_state_unavailable_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_git_state() == {"git_dirty": None, "git_untracked_tree": None}
