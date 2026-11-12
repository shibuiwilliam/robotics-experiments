"""承認付きアクション実行の要求（M8-21、ADR-0014）。

**なぜ `sbp.api` に置かないか。** これは API の入力形式であると同時に、**ワーカーが表から
読み戻すときの契約**でもある。API に置くと、ワーカーが `sbp.api` を import することになり、
層依存の契約（「ワーカーは API を経由しない」）に違反する——実際に import-linter が落ちた。

要求が置かれる層は `actions` である。アクションの意味契約と同じ場所にあるべきで、API は
それを import して受け付けるだけである（`api ← actions` は許される向き）。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ExecutionCommand"]


class ExecutionCommand(BaseModel):
    """アクションの実行要求（`/commands/execute_action` の引数）。

    **承認そのものである。** ``riskClass >= medium`` のアクションは承認必須で（設計書 §9.3）、
    このコマンドを出すことが承認を与えることに当たる。``approved_by`` はロールではなく主体の
    IRI で、来歴に残る。

    **実行の成否をここでは返らない。** 前提検証（三面）とレート制限で落ちることがあり、
    それは適用側が判定して ``learning_decisions.error`` に残す——「受け付けた」と「実行した」は
    別である（ADR-0012 と同じ形）。

    Attributes:
        decision_id: 冪等の鍵。**アクションの二重実行はこの基盤で最悪の失敗である**
            （業務システムを 2 回変える）。UI は再送で同じ値を送ること。
        action_type: アクション型 IRI。契約レジストリに実在しなければ適用時に失敗する。
        target: 対象実体の IRI。
        approved_by: 承認した主体の IRI。
        reason: 実行する理由（人が書く）。**空を許さない**——理由の無い実行は監査で読めない。
        severity: 起因となった不一致の深刻度（承認が必要かの判定に使う。設計書 §9.1 の
            ``severity_at_least_high``）。
        correlation_id: 起因となった不一致の ID（来歴で辿れるようにする）。
        override_reason: 前提未達を人が上書きして実行する理由。**既定は ``None``**——
            上書きは安全ガードを外す操作なので、明示的に理由を書かせる（設計書 §9.3）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    correlation_id: UUID | None = None
    override_reason: str | None = Field(default=None, min_length=1)
