"""不一致を作業項目として持つ。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04

M8-12、ADR-0013。不一致は設計書 §8.2 の「製品」であり、状態機械を持つ作業項目である。

**純粋な投影ではない。** 「状態はイベントの投影」が成立するには、イベントから状態を再構成
できなければならない。しかし起票イベント（ADR-0010）は**通知**であって不一致そのものではない
——`EventEnvelope` は様相と信念を必須に持つ形で、根拠アサーション・状態履歴・深刻度の内訳・
解決・学習信号を入れる場所が無い（`docs/PROGRESS.md` 所見 62）。

したがって**照合ワーカーがこの表に直接書く**（本体を持っているのはそこだけ）。規律の例外なので
理由をここに残す。

**問い合わせに使う列だけを展開し、本体は jsonb で持つ。** `Discrepancy`（pydantic）が契約で、
読み出しで検証する。全フィールドを列に落とすと 20 列を超え、付録 A.2 への加法的な追加ごとに
マイグレーションが必要になる。

**`status_rank` を持つ理由。** 状態の後退を SQL で弾くために、ライフサイクル上の位置を数値で
持つ（`DiscrepancyStatus.rank`）。終端だけを守る形にすると、`Resolved` の行を古い `Open` で
上書きできてしまう——実際にテストで出た（所見 80）。
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """不一致の作業項目表を作る。"""
    op.execute(
        """
        CREATE TABLE discrepancies (
            discrepancy_id uuid        NOT NULL PRIMARY KEY,
            site           text        NOT NULL,
            kind           smallint    NOT NULL CHECK (kind BETWEEN 1 AND 5),
            status         text        NOT NULL,
            status_rank    smallint    NOT NULL CHECK (status_rank BETWEEN 0 AND 4),
            subjects       text[]      NOT NULL,
            severity_score double precision NOT NULL CHECK (severity_score BETWEEN 0 AND 1),
            detected_at    timestamptz NOT NULL,
            terminal       boolean     NOT NULL DEFAULT false,
            payload        jsonb       NOT NULL,
            updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE discrepancies IS "
        "'不一致の作業項目（ADR-0013）。**純粋な投影ではない**——起票イベントは通知であって"
        "不一致そのものではないため、照合ワーカーが直接書く。本体は payload(jsonb)。'"
    )
    # 未解決を深刻度降順に引くための部分索引。UI の不一致キューが最初に見る集合である。
    # **終端状態を除く**のは、解決済みの履歴を毎回走査したくないため
    op.execute(
        "CREATE INDEX discrepancies_queue_idx ON discrepancies "
        "(site, severity_score DESC, detected_at DESC) WHERE NOT terminal"
    )
    # 実体から不一致を引く（信念ビューから「この設備の不一致」へ飛ぶ）。
    # subjects は配列なので GIN にする
    op.execute("CREATE INDEX discrepancies_subjects_idx ON discrepancies USING gin (subjects)")
    # 類型別の集計（運用で「どの類型が増えたか」を見る）
    op.execute(
        "CREATE INDEX discrepancies_kind_idx ON discrepancies (site, kind, detected_at DESC)"
    )


def downgrade() -> None:
    """作業項目表を落とす。"""
    op.execute("DROP TABLE IF EXISTS discrepancies")
