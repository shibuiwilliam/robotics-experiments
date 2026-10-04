"""接地ワーカーのウォーターマークを投影表に持たせる。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04

M8-03。接地ワーカーは「最後に処理した潜在の時刻」を実体ごとに持つが、M7 の実装では
**プロセス内のメモリ**にあった。再起動すると最初から読み直すことになり、

- 過去の潜在を全部読み直して同じ変化点から事象を昇格し直す（重複イベント）か、
- 取りこぼしを避けるために最新から始めて、止まっていた間の潜在を飛ばす

のどちらかになる。どちらも静かに起こるのが問題で、投影表に逃がして「どこまで処理したか」を
再起動を越えて持つ。

**キーに WM 版と階層を含める。** 違う潜在空間・違う階層のウォーターマークは意味が違う
（同じ実体でも潜在の時系列が別物である）。版を上げたときに古いウォーターマークを流用すると、
新しい版の潜在を「もう処理した」ことにしてしまう。キーに版が入っていれば、新しい版は
ウォーターマーク無しの状態から始まる。

**hypertable にしない。** 行数は「ワーカー × 実体 × 版 × 階層」で、時系列ではなく最新状態の
投影である（1 実体 1 行を更新していく）。
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """ウォーターマークの投影表を作る。"""
    op.execute(
        """
        CREATE TABLE grounding_watermarks (
            worker            text        NOT NULL,
            entity            text        NOT NULL,
            wm_version        text        NOT NULL,
            level             text        NOT NULL,
            processed_through timestamptz NOT NULL,
            updated_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (worker, entity, wm_version, level)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE grounding_watermarks IS "
        "'接地ワーカーが「どこまで潜在を処理したか」。再起動を越えて進んだ位置を保つ。"
        "キーに WM 版と階層が入るのは、違う潜在空間のウォーターマークが意味を持たないため。'"
    )
    # 「遅れている実体」を引くための索引。運用で見るのは実体ごとの最新状態である。
    op.execute(
        "CREATE INDEX grounding_watermarks_lag_idx ON grounding_watermarks "
        "(worker, wm_version, level, processed_through)"
    )


def downgrade() -> None:
    """投影表を落とす（イベントログから再構築できる）。"""
    op.execute("DROP TABLE IF EXISTS grounding_watermarks")
