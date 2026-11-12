"""人の判断（不一致の解決・概念の採否）を作業キューとして持つ。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04

M8-08、ADR-0012。UI は `/commands/*` に判断を POST するが、**受けて適用するコードが無かった**。
判断を受け付けること（速い・失わせてはいけない）と適用すること（遅い・失敗しうる）を分け、
API はこの表に積み、`learning-worker` が引いて適用する。

**行を消さない。** 適用に失敗した判断も `failed_at` と `error` を残す——消すと「人が判断した
のに何も起きなかった」状態が説明できない（`quarantine` と `observation_requests` と同じ扱い）。

**判断の内容を jsonb で持つ。** 解決（`resolution`）と概念採否（`curation`）で形が違い、
今後も種類が増える。列に展開すると種類ごとに DDL が必要になる。**種類（`kind`）で分岐する
のはワーカー側**で、pydantic が検証する。
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """判断の作業キューを作る。"""
    op.execute(
        """
        CREATE TABLE learning_decisions (
            decision_id   text        NOT NULL PRIMARY KEY,
            site          text        NOT NULL,
            kind          text        NOT NULL CHECK (kind IN ('resolution', 'curation')),
            subject       text        NOT NULL,
            decided_by    text        NOT NULL,
            payload       jsonb       NOT NULL,
            received_at   timestamptz NOT NULL DEFAULT now(),
            applied_at    timestamptz,
            failed_at     timestamptz,
            attempts      integer     NOT NULL DEFAULT 0,
            error         text,
            artifacts     jsonb
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE learning_decisions IS "
        "'人の判断の作業キュー（ADR-0012）。API が積み、learning-worker が適用する。"
        "行は消さず applied_at / failed_at を立てる。'"
    )
    # 未適用の判断を拾うための部分索引。**古い順に適用する**——人が順に判断したなら、
    # その順で適用するのが期待に合う（後の判断が前の判断を前提にしうる）
    op.execute(
        "CREATE INDEX learning_decisions_pending_idx ON learning_decisions "
        "(site, received_at) WHERE applied_at IS NULL AND failed_at IS NULL"
    )
    # 対象（不一致 / 提案）から判断を引くための索引。UI が「この不一致はどうなったか」を
    # 見るのに使う
    op.execute(
        "CREATE INDEX learning_decisions_subject_idx ON learning_decisions "
        "(site, subject, received_at DESC)"
    )


def downgrade() -> None:
    """判断の作業キューを落とす。"""
    op.execute("DROP TABLE IF EXISTS learning_decisions")
