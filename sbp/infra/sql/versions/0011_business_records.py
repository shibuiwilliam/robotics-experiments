"""業務記録の存在を投影表に持たせる。

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-07

M8-27-2、ADR-0017。期待の検証（記号側）は「この実体にこの型の記録が、期待の作成後にあるか」を
要る。取込は存在イベントを出すだけで、台帳以外の記録は永続しない。業務システムが system of
record のまま、**存在だけを追記専用の投影として持つ**（記録の中身は持たない）。

**キーは (拠点, 記録型, 外部 ID, 記録時刻)。** 同じ伝票の訂正は記録時刻が進むので別の行になり、
再送は ``ON CONFLICT DO NOTHING`` で弾く。**送信の後に書く**（所見 52 と同じ理由）。

**hypertable にしない。** 検証が引くのは「実体 × 記録型 × 期待の作成時刻以降」で、単調に増える点は
保持窓で削る（ADR-0017 の未決）。
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """業務記録の存在の投影表を作る。"""
    op.execute(
        """
        CREATE TABLE business_records (
            site        text        NOT NULL,
            entity      text        NOT NULL,
            record_type text        NOT NULL,
            external_id text        NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY (site, record_type, external_id, recorded_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX business_records_lookup ON business_records "
        "(site, entity, record_type, recorded_at)"
    )
    op.execute(
        "COMMENT ON TABLE business_records IS "
        "'取込が送った業務記録の存在（追記専用の投影）。期待の記号側の検証が引く。"
        "記録の中身は持たない。'"
    )


def downgrade() -> None:
    """投影表を落とす（業務システムの読み直しで再構築できる）。"""
    op.execute("DROP TABLE IF EXISTS business_records")
