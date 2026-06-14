"""Shared pytest fixtures.

Environment isolation (IMPROVEMENT R5): the developer shell may export MWS_*
variables (e.g. direnv loading .env with MWS_CLOUD_MODE=live), which would leak
into pydantic-settings and break the mock-by-default, deterministic test
contract (CLAUDE.md §8). Scrub them for every test except those explicitly
marked @pytest.mark.live, which need the real environment/credentials.
"""

from __future__ import annotations

import os

import pytest

_SCRUB_PREFIX = "MWS_"
_SCRUB_VARS = ("GOOGLE_API_KEY",)


@pytest.fixture(autouse=True)
def _scrub_mws_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        return  # live tests intentionally use the real environment
    for var in list(os.environ):
        if var.startswith(_SCRUB_PREFIX) or var in _SCRUB_VARS:
            monkeypatch.delenv(var, raising=False)
    # The default vector backend is Elasticsearch (a running cluster), but the
    # test suite must stay offline and deterministic — force the in-memory
    # store for every non-live test. This is the "mock test" boundary: only
    # the pytest suite uses memory; real scenario runs use ES.
    monkeypatch.setenv("MWS_VECTOR_BACKEND", "memory")
