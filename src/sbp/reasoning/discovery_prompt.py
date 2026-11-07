"""概念提案の LLM 入出力（設計書 §8.6 の段階 3、M8-09、ADR-0019）。

G6 の統合テストの中にあった組み立てを運用コードへ移したもの。**メッセージは決定的に組む**——
fixture の鍵はメッセージ・スキーマ・プロンプト版の内容ハッシュ
（:func:`sbp.llm.base.request_digest`）なので、ここが非決定的（順序が不定、時刻や乱数が入る）だと
fixture が当たらない。

潜在ベクトルそのものは渡さない。数値の羅列は LLM の役に立たず、ハッシュを不安定にするだけである。
渡すのは signal の説明（件数・強さ・どの入口か）と既存のフルーエント型の一覧。

**出力は pydantic で検証してから提案にする**（設計書 §7.5 の順序）。崩れた出力を下流に流さない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from sbp.llm.base import Message, Role

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.reasoning.discovery import DiscoverySignal

__all__ = ["PROPOSAL_SCHEMA", "ProposalOutput", "render_messages"]

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "definition": {"type": "string"},
        "superclass": {"type": "string"},
        "mutually_exclusive_with": {"type": "array", "items": {"type": "string"}},
        "initiates": {"type": "array", "items": {"type": "string"}},
        "terminates": {"type": "array", "items": {"type": "string"}},
        "rejected_alternatives": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "definition", "rejected_alternatives"],
}
"""LLM に求める JSON Schema（fixture の鍵に入る。変えると fixture を録り直す）。"""


class ProposalOutput(BaseModel):
    """LLM の出力の契約。未知のフィールドは弾かず捨てる（``evidence_note`` などの説明用）。

    Attributes:
        name: 概念のローカル名。
        definition: 一文の定義（成立条件）。
        superclass: 上位概念のローカル名。
        mutually_exclusive_with: 排他になる既存フルーエント型。
        initiates: 成立させる既存の事象型。
        terminates: 不成立にする既存の事象型。
        rejected_alternatives: 既存概念で足りなかった点。**空は「既存の語彙で足りる」という答えで、
            出力としては正しい**（提案にしない判断は :class:`ConceptProposal` が行う）。
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    superclass: str = ""
    mutually_exclusive_with: tuple[str, ...] = ()
    initiates: tuple[str, ...] = ()
    terminates: tuple[str, ...] = ()
    rejected_alternatives: tuple[str, ...] = ()


def render_messages(
    prompt_body: str, signals: Sequence[DiscoverySignal], *, existing: Sequence[str]
) -> list[Message]:
    """LLM へのメッセージを**決定的に**組み立てる。

    Args:
        prompt_body: プロンプト本文（``PromptRegistry`` の版指定）。
        signals: 発見 signal。
        existing: 既存のフルーエント型のローカル名（並びは内部でソートする）。
    """
    lines = [f"- {signal.trigger.value}: {signal.note}" for signal in signals]
    return [
        Message(role=Role.SYSTEM, content=prompt_body),
        Message(
            role=Role.USER,
            content=(
                "既存のフルーエント型: "
                + ", ".join(sorted(existing))
                + "\n発見された signal:\n"
                + "\n".join(lines)
            ),
        ),
    ]
