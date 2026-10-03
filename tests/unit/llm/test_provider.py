"""LLM 層の単体テスト（LLM 利用ポリシー、M6-01〜M6-03）。

重点:

1. **replay が fixture 欠落で失敗する** — 黙って live に落ちると CI が非決定的になり課金が出る。
2. **用途が列挙である** — 「汎用の LLM 呼び出し」を作れないようにする。
3. **ハッシュが入力の全部を含む** — プロンプトを変えたのにテストが通る状態を防ぐ。
4. **プロンプトの latest を許さない** — 当時の出力を当時のプロンプトで説明できなくなる。
5. **routing が全用途を宣言している** — 宣言漏れが既定で動くと設定から読めない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from sbp.core.errors import FixtureMissingError, LlmError, StructuredOutputError
from sbp.llm import (
    LlmMode,
    LlmTask,
    Message,
    PromptRegistry,
    Provider,
    ProviderResponse,
    ReplayProvider,
    Role,
    TokenUsage,
    UsageLedger,
    fixture_path,
    load_routing,
    request_digest,
    resolve_provider,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


def _messages() -> list[Message]:
    return [
        Message(role=Role.SYSTEM, content="あなたはオントロジー設計の助手である"),
        Message(role=Role.USER, content="クラスタ 20 件、プローブ最大活性 0.05"),
    ]


def _write_fixture(root: Path, task: LlmTask, digest: str, content: dict[str, Any]) -> Path:
    path = fixture_path(task, digest, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "content": content,
                "provider": "anthropic",
                "model": "fixture-model",
                "prompt_version": "1.0.0",
                "prompt_hash": "abcd1234",
                "usage": {"prompt": 120, "completion": 45},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class _StubLive:
    """live プロバイダの代わり（ネットワークを使わない）。"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub"

    def complete_structured(
        self,
        messages: Any,
        *,
        schema: dict[str, Any],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        del messages, schema
        self.calls += 1
        return ProviderResponse(
            content={"name": "FromLive"},
            provider="stub",
            model="stub-model",
            prompt_version=prompt_version,
            prompt_hash="live0000",
            usage=TokenUsage(prompt=10, completion=5),
            task=task,
        )

    def embed(self, texts: Any, *, task: LlmTask) -> list[list[float]]:
        del task
        return [[1.0, 0.0] for _ in texts]


class TestMessages:
    def test_an_empty_message_is_rejected(self) -> None:
        with pytest.raises(LlmError, match="must have content"):
            Message(role=Role.USER, content="   ")


class TestTaskEnumeration:
    def test_only_the_four_permitted_uses_exist(self) -> None:
        """不変条件 11: LLM の用途は概念提案・文書抽出・説明の文章化・NL→クエリに限る。"""
        assert {task.value for task in LlmTask} == {
            "concept_proposal",
            "document_extraction",
            "explanation_narrative",
            "query_translation",
        }


class TestRequestDigest:
    def test_it_changes_with_the_messages(self) -> None:
        first = request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0")
        other = [*_messages(), Message(role=Role.USER, content="追加")]
        assert first != request_digest(other, schema=SCHEMA, prompt_version="1.0.0")

    def test_it_changes_with_the_schema(self) -> None:
        other = {**SCHEMA, "properties": {"name": {"type": "string"}, "extra": {"type": "number"}}}
        assert request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0") != request_digest(
            _messages(), schema=other, prompt_version="1.0.0"
        )

    def test_it_changes_with_the_prompt_version(self) -> None:
        """プロンプトを変えたのに同じ fixture が再生されると、変更がテストに現れない。"""
        assert request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0") != request_digest(
            _messages(), schema=SCHEMA, prompt_version="1.1.0"
        )

    def test_it_is_stable_for_the_same_input(self) -> None:
        assert request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0") == request_digest(
            _messages(), schema=SCHEMA, prompt_version="1.0.0"
        )


class TestReplayMode:
    def test_a_missing_fixture_fails_loudly(self, tmp_path: Path) -> None:
        """黙って live に落ちると CI が非決定的になり、課金が出て、しかも通ってしまう。"""
        provider = ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path)
        with pytest.raises(FixtureMissingError, match="does not fall back to the network"):
            provider.complete_structured(
                _messages(),
                schema=SCHEMA,
                task=LlmTask.CONCEPT_PROPOSAL,
                prompt_version="1.0.0",
            )

    def test_a_present_fixture_is_replayed(self, tmp_path: Path) -> None:
        digest = request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0")
        _write_fixture(tmp_path, LlmTask.CONCEPT_PROPOSAL, digest, {"name": "CavitationOnset"})
        provider = ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path)
        response = provider.complete_structured(
            _messages(), schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
        )
        assert response.content["name"] == "CavitationOnset"
        assert response.from_fixture
        assert response.usage.total == 165

    def test_replay_needs_no_live_provider(self, tmp_path: Path) -> None:
        assert ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path).name == "replay"

    def test_other_modes_need_a_live_provider(self, tmp_path: Path) -> None:
        with pytest.raises(LlmError, match="needs a live provider"):
            ReplayProvider(mode=LlmMode.LIVE, root=tmp_path)

    def test_replay_embeddings_are_deterministic(self, tmp_path: Path) -> None:
        """同じ文字列が同じベクトルになり、違う文字列が違うベクトルになること。"""
        provider = ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path)
        first = provider.embed(["ポンプ停止", "振動異常"], task=LlmTask.CONCEPT_PROPOSAL)
        second = provider.embed(["ポンプ停止", "振動異常"], task=LlmTask.CONCEPT_PROPOSAL)
        assert first == second
        assert first[0] != first[1]

    def test_the_mode_declares_whether_it_uses_the_network(self) -> None:
        assert not LlmMode.REPLAY.uses_network
        assert LlmMode.RECORD.uses_network
        assert LlmMode.LIVE.uses_network


class TestRecordMode:
    def test_it_saves_a_new_fixture(self, tmp_path: Path) -> None:
        live = _StubLive()
        provider = ReplayProvider(mode=LlmMode.RECORD, live=live, root=tmp_path)
        response = provider.complete_structured(
            _messages(), schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
        )
        digest = request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0")
        assert fixture_path(LlmTask.CONCEPT_PROPOSAL, digest, root=tmp_path).is_file()
        assert response.content["name"] == "FromLive"
        assert live.calls == 1

    def test_it_does_not_overwrite_an_existing_fixture(self, tmp_path: Path) -> None:
        """上書きを既定にすると、応答の変化が差分として現れないまま取り込まれる。"""
        digest = request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0")
        _write_fixture(tmp_path, LlmTask.CONCEPT_PROPOSAL, digest, {"name": "Original"})
        live = _StubLive()
        provider = ReplayProvider(mode=LlmMode.RECORD, live=live, root=tmp_path)
        response = provider.complete_structured(
            _messages(), schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
        )
        assert response.content["name"] == "Original"
        assert live.calls == 0

    def test_live_mode_ignores_fixtures(self, tmp_path: Path) -> None:
        digest = request_digest(_messages(), schema=SCHEMA, prompt_version="1.0.0")
        _write_fixture(tmp_path, LlmTask.CONCEPT_PROPOSAL, digest, {"name": "Original"})
        live = _StubLive()
        provider = ReplayProvider(mode=LlmMode.LIVE, live=live, root=tmp_path)
        response = provider.complete_structured(
            _messages(), schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
        )
        assert response.content["name"] == "FromLive"
        assert live.calls == 1


class TestResolveProvider:
    def test_replay_resolves_without_a_factory(self) -> None:
        assert resolve_provider(mode=LlmMode.REPLAY).name == "replay"

    def test_live_without_a_factory_is_refused(self) -> None:
        with pytest.raises(LlmError, match="needs a live provider factory"):
            resolve_provider(mode=LlmMode.LIVE)

    def test_the_replay_provider_satisfies_the_protocol(self, tmp_path: Path) -> None:
        assert isinstance(ReplayProvider(mode=LlmMode.REPLAY, root=tmp_path), Provider)


class TestStructuredValidation:
    def _response(self, content: dict[str, Any]) -> ProviderResponse:
        return ProviderResponse(
            content=content,
            provider="anthropic",
            model="m",
            prompt_version="1.0.0",
            prompt_hash="h",
            usage=TokenUsage(),
            task=LlmTask.CONCEPT_PROPOSAL,
        )

    def test_a_valid_payload_passes(self) -> None:
        class Proposal(BaseModel):
            name: str

        assert self._response({"name": "X"}).validated(Proposal).name == "X"

    def test_a_broken_payload_is_rejected(self) -> None:
        """崩れた出力を下流に流すと「推定」としても扱えない（設計書 §7.5）。"""

        class Proposal(BaseModel):
            name: str

        with pytest.raises(StructuredOutputError, match="does not match Proposal"):
            self._response({"wrong": 1}).validated(Proposal)

    def test_the_provenance_has_the_three_required_fields(self) -> None:
        """LLM 利用ポリシー: provider / model / prompt_version(hash) を来歴に残す。"""
        provenance = self._response({"name": "X"}).provenance()
        assert set(provenance) == {"provider", "model", "prompt_version"}
        assert "@" in provenance["prompt_version"]


class TestUsageLedger:
    def test_usage_is_tracked_per_task(self) -> None:
        """合計だけでは、どの用途がコストを使っているかが分からない。"""
        ledger = UsageLedger()
        for task in (LlmTask.CONCEPT_PROPOSAL, LlmTask.CONCEPT_PROPOSAL, LlmTask.QUERY_TRANSLATION):
            ledger.record(
                ProviderResponse(
                    content={},
                    provider="p",
                    model="m",
                    prompt_version="1.0.0",
                    prompt_hash="h",
                    usage=TokenUsage(prompt=10, completion=5),
                    task=task,
                )
            )
        assert ledger.by_task[LlmTask.CONCEPT_PROPOSAL].total == 30
        assert ledger.by_task[LlmTask.QUERY_TRANSLATION].total == 15
        assert ledger.total == 45


class TestPromptRegistry:
    def test_the_repository_prompts_load(self) -> None:
        registry = PromptRegistry()
        template = registry.get(LlmTask.CONCEPT_PROPOSAL, "1.0.0")
        assert "自動採用" in template.body or "採否" in template.body
        assert "@" in template.provenance

    def test_latest_is_forbidden(self) -> None:
        """当時の出力を当時のプロンプトで説明できなくなる。"""
        with pytest.raises(LlmError, match="'latest' is forbidden"):
            PromptRegistry().get(LlmTask.CONCEPT_PROPOSAL, "latest")

    def test_an_unknown_version_lists_what_exists(self) -> None:
        with pytest.raises(LlmError, match="available:"):
            PromptRegistry().get(LlmTask.CONCEPT_PROPOSAL, "9.9.9")

    def test_the_digest_changes_with_the_body(self, tmp_path: Path) -> None:
        """版を据え置いて本文を書き換えると、過去の出力を再現できなくなる。"""
        directory = tmp_path / LlmTask.CONCEPT_PROPOSAL.value
        directory.mkdir(parents=True)
        (directory / "1.0.0.md").write_text("最初の本文", encoding="utf-8")
        registry = PromptRegistry(root=tmp_path)
        first = registry.get(LlmTask.CONCEPT_PROPOSAL, "1.0.0").digest
        (directory / "1.0.0.md").write_text("書き換えた本文", encoding="utf-8")
        assert registry.get(LlmTask.CONCEPT_PROPOSAL, "1.0.0").digest != first

    def test_the_extraction_prompt_requires_evidence_spans(self) -> None:
        """LLM 利用ポリシー: 文書抽出のスキーマは根拠スパンを必須にする。"""
        body = PromptRegistry().get(LlmTask.DOCUMENT_EXTRACTION, "1.0.0").body
        assert "根拠スパン" in body
        assert "inferred" in body


class TestRouting:
    def test_every_task_is_declared(self) -> None:
        """宣言漏れを既定で動かすと、どの用途がどこに回っているか設定から読めない。"""
        routing = load_routing()
        assert set(routing) == set(LlmTask)

    def test_the_default_provider_resolves_from_settings(self) -> None:
        routing = load_routing()
        entry = routing[LlmTask.CONCEPT_PROPOSAL]
        assert entry.provider == "default"
        assert entry.resolved_provider("anthropic") == "anthropic"

    def test_a_missing_task_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "routing.yaml"
        path.write_text(
            "tasks:\n  concept_proposal:\n    provider: default\n    prompt_version: 1.0.0\n",
            encoding="utf-8",
        )
        with pytest.raises(LlmError, match="does not declare task"):
            load_routing(path)

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(LlmError, match="routing file not found"):
            load_routing(tmp_path / "absent.yaml")
