"""VCR core — cassette keying, storage, and mode dispatch."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from clients.vcr.exceptions import BudgetExceeded, MissingCassette
from config import load_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]


class VcrMode(StrEnum):
    replay = "replay"
    record = "record"
    passthrough = "passthrough"


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")


def canonical_hash(model_id: str, request: dict[str, Any]) -> str:
    """Stable key for (model, normalized request). Excludes nothing the caller passes — the caller
    must NOT include time/random in the request (CLAUDE.md §8)."""
    payload = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(f"{model_id}\n{payload}".encode()).hexdigest()
    return digest[:20]


class VCR:
    """Record/replay proxy for a single provider family. Cassettes are JSON files on disk."""

    def __init__(
        self, mode: VcrMode | str | None = None, cassette_dir: Path | str | None = None
    ) -> None:
        reg = load_registry()
        self.mode = VcrMode(mode) if mode is not None else VcrMode(reg.vcr_mode())
        if cassette_dir is not None:
            self._dir = Path(cassette_dir)
        else:
            self._dir = _REPO_ROOT / str(reg.require("vcr.cassette_dir"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._daily_cap = float(reg.get("budget.daily_usd_cap", 0.0))
        self._stop_on_exceed = bool(reg.get("budget.stop_on_exceed", True))
        self._spent = 0.0
        self._live_calls = 0

    @property
    def live_calls(self) -> int:
        return self._live_calls

    @property
    def spent_usd(self) -> float:
        return self._spent

    def _path(self, model_id: str, key: str) -> Path:
        return self._dir / f"{_slug(model_id)}-{key}.json"

    def interact(
        self,
        model_id: str,
        request: dict[str, Any],
        live_fn: Callable[[], Any],
        *,
        cost_usd: float = 0.0,
    ) -> Any:
        """Return a response for ``request`` under the current mode.

        replay: cassette or MissingCassette. record: cassette or live+save. passthrough: live.
        """
        key = canonical_hash(model_id, request)
        path = self._path(model_id, key)

        if self.mode is VcrMode.replay:
            if path.exists():
                return self._load(path)["response"]
            raise MissingCassette(
                f"no cassette for model={model_id} key={key} (run `make test-live` to record)"
            )

        if self.mode is VcrMode.record and path.exists():
            return self._load(path)["response"]

        # record (miss) or passthrough -> live call
        self._guard_budget(cost_usd)
        response = live_fn()
        self._live_calls += 1
        self._spent += cost_usd
        if self.mode is VcrMode.record:
            self._save(path, model_id, request, response)
        return response

    def _guard_budget(self, cost_usd: float) -> None:
        if (
            self._stop_on_exceed
            and self._daily_cap > 0
            and self._spent + cost_usd > self._daily_cap
        ):
            raise BudgetExceeded(
                f"live call would exceed daily cap ${self._daily_cap:.2f} (spent ${self._spent:.4f})"
            )

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    @staticmethod
    def _save(path: Path, model_id: str, request: dict[str, Any], response: Any) -> None:
        path.write_text(
            json.dumps(
                {"model": model_id, "request": request, "response": response},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
