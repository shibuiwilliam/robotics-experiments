"""プロンプトの版管理と routing（LLM 利用ポリシー、M6-03）。

> プロンプトは ``src/sbp/llm/prompts/<task>/<version>.md``。変更は version を上げ、hash を来歴に
> 残す。``task`` 別の既定プロバイダは ``routing.yaml``。モデル ID は ``.env`` からのみ読む。

**版を据え置いて本文を書き換えられないようにする。** ハッシュを来歴に残すので、同じ版で本文が
違えば過去の出力を再現できない。``PromptRegistry`` は版とハッシュを対で返し、呼び出し側が
必ず両方を記録する形にしている。

**モデル ID を routing に書かない。** ``.env`` から読む（規約）。routing が決めるのは「どの用途を
どのプロバイダに回すか」と「どのプロンプト版を使うか」だけである。モデル ID を設定ファイルに
書くと、モデルの差し替えが設定変更ではなくファイル変更になり、環境ごとの切り替えができない。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from sbp.core.errors import LlmError
from sbp.core.logging import get_logger
from sbp.llm.base import LlmTask

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["PromptRegistry", "PromptTemplate", "TaskRouting", "load_routing"]

log = get_logger(__name__)

_PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
"""プロンプトの置き場。"""

_ROUTING_PATH = Path(__file__).resolve().parent / "routing.yaml"
"""routing の置き場。"""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """プロンプト 1 つ。

    Attributes:
        task: 用途。
        version: 版。
        body: 本文。
        digest: 本文のハッシュ（来歴に残す）。
    """

    task: LlmTask
    version: str
    body: str
    digest: str

    @property
    def provenance(self) -> str:
        """来歴に入れる文字列（``<版>@<ハッシュ>``）。"""
        return f"{self.version}@{self.digest}"


class PromptRegistry:
    """プロンプトを版指定で読む。

    ``latest`` を許さないのは、モデル版と同じ理由である（ADR-0007 の周辺で述べた「今日の出力を
    当時のプロンプトで説明できなくなる」）。
    """

    def __init__(self, root: Path | None = None) -> None:
        """Args:
        root: プロンプトの根（既定はパッケージ同梱）。
        """
        self._root = root or _PROMPT_ROOT

    def get(self, task: LlmTask, version: str) -> PromptTemplate:
        """用途と版でプロンプトを読む。

        Args:
            task: 用途。
            version: 版（``latest`` は不可）。

        Raises:
            LlmError: ``latest`` を指定した、またはファイルが無い場合。
        """
        if version == "latest":
            raise LlmError(
                "prompt version 'latest' is forbidden; a belief produced today must stay "
                "explainable by the prompt that produced it"
            )
        path = self._root / task.value / f"{version}.md"
        if not path.is_file():
            available = self.versions(task)
            raise LlmError(
                f"no prompt for task {task.value} version {version} at {path}; "
                f"available: {list(available)}"
            )
        body = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        return PromptTemplate(task=task, version=version, body=body, digest=digest)

    def versions(self, task: LlmTask) -> tuple[str, ...]:
        """用途に対して用意されている版（昇順）。

        Args:
            task: 用途。
        """
        directory = self._root / task.value
        if not directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in directory.glob("*.md")))


@dataclass(frozen=True, slots=True)
class TaskRouting:
    """用途 1 つの routing。

    Attributes:
        task: 用途。
        provider: プロバイダ名（``default`` なら ``.env`` の ``SBP_LLM_DEFAULT``）。
        prompt_version: 使うプロンプトの版。
    """

    task: LlmTask
    provider: str
    prompt_version: str

    def resolved_provider(self, default: str) -> str:
        """``default`` を実際のプロバイダ名に解決する。

        Args:
            default: ``.env`` の既定プロバイダ。
        """
        return default if self.provider == "default" else self.provider


def load_routing(path: Path | None = None) -> Mapping[LlmTask, TaskRouting]:
    """``routing.yaml`` を読む。

    **全用途が宣言されていることを要求する。** 宣言漏れを「既定で動かす」と、どの用途が
    どのプロバイダに回っているかが設定から読めなくなる。

    Args:
        path: routing のパス（既定はパッケージ同梱）。

    Raises:
        LlmError: ファイルが無い、または宣言されていない用途がある場合。
    """
    source = path or _ROUTING_PATH
    if not source.is_file():
        raise LlmError(f"routing file not found: {source}")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    declared = payload.get("tasks", {})
    routing: dict[LlmTask, TaskRouting] = {}
    for task in LlmTask:
        entry = declared.get(task.value)
        if entry is None:
            raise LlmError(
                f"routing.yaml does not declare task {task.value}; every task must say which "
                "provider and prompt version it uses (an undeclared task would silently take a "
                "default that nobody can read from the configuration)"
            )
        routing[task] = TaskRouting(
            task=task,
            provider=str(entry["provider"]),
            prompt_version=str(entry["prompt_version"]),
        )
    log.debug("llm.routing_loaded", tasks=[task.value for task in routing])
    return routing
