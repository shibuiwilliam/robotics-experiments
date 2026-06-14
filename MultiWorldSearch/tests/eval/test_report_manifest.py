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
    "vector_backend",
    "git_dirty_paths",
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


# --- M21: which vector backend produced the run ---


def test_manifest_records_memory_backend_without_es_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pytest suite is forced to in-memory (conftest); the manifest says so
    and carries no misleading ES-only fields."""
    monkeypatch.setenv("MWS_VECTOR_BACKEND", "memory")
    manifest = create_manifest(run_id="r1", scenario="test", seed=0)
    assert manifest["vector_backend"] == "memory"
    assert "elasticsearch_url" not in manifest
    assert "elasticsearch_index" not in manifest


def test_manifest_records_elasticsearch_backend_with_url_and_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ES runs additionally record the URL and the index naming convention
    (a `*` pattern — per-run indices are uuid-suffixed and ephemeral). No live
    Elasticsearch is needed: create_manifest only reads settings."""
    monkeypatch.setenv("MWS_VECTOR_BACKEND", "elasticsearch")
    monkeypatch.setenv("MWS_ELASTICSEARCH_URL", "http://es-host:9200")
    monkeypatch.setenv("MWS_DEFAULT_EMBEDDING_SPACE", "gemini2-768-v2")
    monkeypatch.setenv("MWS_EMBEDDING_DIMS", "768")
    manifest = create_manifest(run_id="r1", scenario="test", seed=0)
    assert manifest["vector_backend"] == "elasticsearch"
    assert manifest["elasticsearch_url"] == "http://es-host:9200"
    idx = manifest["elasticsearch_index"]
    assert idx.startswith("mws-vectors-gemini2-768-v2-768d")
    assert idx.endswith("-*")


# --- M22: embedding fields follow the run's actual embedder, not settings ---


class _FakeEmbedder:
    """Stands in for the run's embedder with a space/dims that DIFFER from
    settings — the live-mode drift M22 guards against."""

    def __init__(self, space: object, dims: int) -> None:
        self.space = space
        self.dims = dims


def test_manifest_embedding_fields_follow_embedder_not_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an embedder supplied, embedding_space/dims and the ES index pattern
    reflect the EMBEDDER (the store's true namespace), even when settings say
    something else. This fails on the pre-M22 code (which read settings)."""
    from mws.core.types import EmbeddingSpace

    # Settings deliberately disagree with the embedder (stale env / drift).
    monkeypatch.setenv("MWS_VECTOR_BACKEND", "elasticsearch")
    monkeypatch.setenv("MWS_DEFAULT_EMBEDDING_SPACE", "mock-128-v1")
    monkeypatch.setenv("MWS_EMBEDDING_DIMS", "128")
    embedder = _FakeEmbedder(EmbeddingSpace.GEMINI_768_V2, 768)

    manifest = create_manifest(run_id="r1", scenario="test", seed=0, embedder=embedder)
    assert manifest["embedding_space"] == EmbeddingSpace.GEMINI_768_V2
    assert manifest["embedding_dims"] == 768
    # ES index pattern matches the EMBEDDER's space/dims, not settings'.
    assert manifest["elasticsearch_index"].startswith("mws-vectors-gemini2-768-v2-768d")
    assert "mock-128" not in manifest["elasticsearch_index"]


def test_manifest_falls_back_to_settings_without_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No embedder → settings-based values (backward compatible for the eval
    CLIs / call sites that have no single embedder)."""
    monkeypatch.setenv("MWS_VECTOR_BACKEND", "memory")
    monkeypatch.setenv("MWS_DEFAULT_EMBEDDING_SPACE", "mock-768-v1")
    monkeypatch.setenv("MWS_EMBEDDING_DIMS", "768")
    manifest = create_manifest(run_id="r1", scenario="test", seed=0)
    assert str(manifest["embedding_space"]) == "mock-768-v1"
    assert manifest["embedding_dims"] == 768


def _fake_git(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_git_state_clean_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git(monkeypatch, "")
    assert get_git_state() == {
        "git_dirty": False,
        "git_untracked_tree": False,
        "git_dirty_paths": [],
    }


def test_git_state_dirty_tracked_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git(monkeypatch, " M mws/cli.py\n?? notes.txt\n")
    state = get_git_state()
    assert state["git_dirty"] is True
    assert state["git_untracked_tree"] is False
    # M20: the manifest alone shows WHAT was dirty.
    assert state["git_dirty_paths"] == [" M mws/cli.py", "?? notes.txt"]


def test_git_state_dirty_paths_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git(monkeypatch, "".join(f" M f{i}.py\n" for i in range(25)))
    state = get_git_state()
    assert len(state["git_dirty_paths"]) == 21  # 20 paths + 1 truncation marker
    assert state["git_dirty_paths"][-1].startswith("... (+5 more)")


def test_git_state_entirely_untracked_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The M15 incident: the whole MWS tree untracked → the SHA describes
    NONE of the code that ran, and the manifest must say so."""
    _fake_git(monkeypatch, "?? ./\n")
    state = get_git_state()
    assert state["git_dirty"] is True
    assert state["git_untracked_tree"] is True
    assert state["git_dirty_paths"] == ["?? ./"]


def test_git_state_unavailable_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_git_state() == {
        "git_dirty": None,
        "git_untracked_tree": None,
        "git_dirty_paths": None,
    }
