"""概念提案を作業項目として持つ。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04

M8-19、ADR-0015。UI に概念 curation の画面があるが**読む先が無かった**（存在しないトピックを
読んでいた。所見 85）。不一致（0006）と期待（0007）と同じ形で、問い合わせ列 + `payload jsonb`。

**潜在の重心を入れない**（ADR-0015）。`LatentCluster.centroid` は潜在索引のもので、ここに
置くと再接地で片方だけが古くなる。代わりに**判断に要る 2 つのスカラー**
（`support_count` / `coverage`）を書き込み時に計算して列に出す——UI の curation 検査
（設計書 §11.2）がこれを使い、読み出しでは計算できない。

**`status_rank` で後退を弾く**（所見 80 と同じ対処）。採否が入った提案を `proposed` に
戻せない。
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """概念提案の作業項目表を作る。"""
    op.execute(
        """
        CREATE TABLE concept_proposals (
            proposal_id   uuid        NOT NULL PRIMARY KEY,
            site          text        NOT NULL,
            name          text        NOT NULL,
            status        text        NOT NULL
                CHECK (status IN ('proposed', 'accepted', 'revised', 'rejected')),
            status_rank   smallint    NOT NULL CHECK (status_rank BETWEEN 0 AND 1),
            superclass    text        NOT NULL,
            support_count integer     NOT NULL CHECK (support_count >= 0),
            coverage      real        NOT NULL CHECK (coverage BETWEEN 0 AND 1),
            proposed_at   timestamptz,
            payload       jsonb       NOT NULL,
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # curation の待ち行列は「未採否のものを、支持の多い順」で見る——偶然のクラスタより
    # 標本の多い提案を先に判断する（設計書 §11.2）
    op.execute(
        """
        CREATE INDEX concept_proposals_pending
            ON concept_proposals (site, support_count DESC, proposed_at DESC)
            WHERE status = 'proposed'
        """
    )
    op.execute("CREATE INDEX concept_proposals_by_name ON concept_proposals (site, name)")


def downgrade() -> None:
    """表を落とす。"""
    op.execute("DROP TABLE concept_proposals")
