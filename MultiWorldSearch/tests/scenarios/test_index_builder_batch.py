"""IMPROVEMENT P9/E3 — Batch API offline indexing (unit tests, fake client).

The live end-to-end (real job submit/poll/equivalence) is in
tests/embedding/test_teacher_live.py behind @pytest.mark.live.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mws.scenarios.index_builder import (
    build_index_batch_api,
    extract_batch_vectors,
    poll_batch_job,
    submit_embedding_batch,
)


class _FakeBatches:
    def __init__(self, states: list[str]) -> None:
        self._states = list(states)
        self.created: list[dict] = []
        self.get_calls = 0

    def create_embeddings(self, *, model: str, src, config) -> SimpleNamespace:
        self.created.append({"model": model, "src": src, "config": config})
        return SimpleNamespace(name="batches/fake-job-1", state="JOB_STATE_PENDING")

    def get(self, *, name: str) -> SimpleNamespace:
        state = self._states[min(self.get_calls, len(self._states) - 1)]
        self.get_calls += 1
        return SimpleNamespace(name=name, state=state, dest=None)


def test_submit_embedding_batch_builds_inlined_request() -> None:
    from google.genai import types

    fake = _FakeBatches(states=[])
    client = SimpleNamespace(batches=fake)
    job = submit_embedding_batch(client, "gemini-embedding-2", ["doc one", "doc two"], 768)

    assert job.name == "batches/fake-job-1"
    assert len(fake.created) == 1
    src = fake.created[0]["src"]
    assert isinstance(src, types.EmbeddingsBatchJobSource)
    assert src.inlined_requests is not None
    contents = src.inlined_requests.contents
    assert [c.parts[0].text for c in contents] == ["doc one", "doc two"]
    assert src.inlined_requests.config.output_dimensionality == 768


def test_poll_batch_job_returns_on_terminal_state() -> None:
    fake = _FakeBatches(states=["JOB_STATE_RUNNING", "JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"])
    client = SimpleNamespace(batches=fake)
    job = poll_batch_job(client, "batches/fake-job-1", poll_interval=0.001, timeout=10.0)
    assert "JOB_STATE_SUCCEEDED" in str(job.state)
    assert fake.get_calls == 3


def test_poll_batch_job_timeout_is_resumable_not_an_error() -> None:
    fake = _FakeBatches(states=["JOB_STATE_RUNNING"])
    client = SimpleNamespace(batches=fake)
    job = poll_batch_job(client, "batches/fake-job-1", poll_interval=0.001, timeout=0.0)
    assert "JOB_STATE_RUNNING" in str(job.state)


def _fake_finished_job(vectors: list[list[float]], tokens: list[int]) -> SimpleNamespace:
    responses = [
        SimpleNamespace(
            response=SimpleNamespace(embedding=SimpleNamespace(values=v), token_count=t),
            error=None,
        )
        for v, t in zip(vectors, tokens, strict=True)
    ]
    return SimpleNamespace(
        state="JOB_STATE_SUCCEEDED",
        dest=SimpleNamespace(inlined_embed_content_responses=responses),
    )


def test_extract_batch_vectors_returns_vectors_and_measured_tokens() -> None:
    job = _fake_finished_job([[0.1, 0.2], [0.3, 0.4]], tokens=[7, 5])
    vectors, measured = extract_batch_vectors(job, n_expected=2)
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert measured == 12


def test_extract_batch_vectors_count_mismatch_raises() -> None:
    job = _fake_finished_job([[0.1]], tokens=[1])
    with pytest.raises(RuntimeError, match="returned 1 responses for 2"):
        extract_batch_vectors(job, n_expected=2)


def test_build_index_batch_api_refuses_mock_mode() -> None:
    """The Batch API is a real cloud job — mock mode must refuse loudly."""
    with pytest.raises(ValueError, match="requires MWS_CLOUD_MODE=live"):
        build_index_batch_api(config_path="configs/index/default.yaml", seed=0)
