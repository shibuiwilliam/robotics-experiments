"""初期スキーマ: 冪等性の投影、潜在索引、メトリクス。

Revision ID: 0001
Revises:
Create Date: 2026-10-03

設計書 §6.1（冪等性）、§6.5（潜在索引）、§12.3（可観測性）に対応する。

このマイグレーションが作るものは「イベントログからの投影」だけである。信念そのものは
知識グラフ（Oxigraph）が持ち、ここには置かない。状態テーブルを直接更新して整合させる
ことは禁止（CLAUDE.md）。

時刻列は ``timestamptz``、区間は ``tstzrange``（knowledge ルール）。
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# 潜在ベクトルの次元。WM 版ごとに固定し、次元が変わる更新は新テーブルを作って再接地ジョブで
# 移行する（knowledge ルール）。表の命名と版 → 表の対応は 0002（latent_index_d<次元> と
# latent_index_registry）で決め直した。
LATENT_DIM = 64


def upgrade() -> None:
    """拡張・テーブル・hypertable・索引を作る。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- 冪等性の投影（設計書 §6.1）------------------------------------------
    # consumer は at-least-once。副作用の前にこの表を見て event_id の重複を弾く。
    op.execute(
        """
        CREATE TABLE processed_events (
            event_id        uuid        NOT NULL,
            consumer_group  text        NOT NULL,
            topic           text        NOT NULL,
            subject         text        NOT NULL,
            processed_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (consumer_group, event_id)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE processed_events IS "
        "'消費済みイベントの記録。consumer ごとに event_id で冪等化する（不変条件 4）。'"
    )
    op.execute(
        "CREATE INDEX processed_events_subject_idx ON processed_events (subject, processed_at DESC)"
    )

    # ---- 潜在索引（設計書 §6.5）---------------------------------------------
    # (実体, 時刻, 階層) をキーに潜在ベクトルを持つ。level と wm_version で絞ってから
    # 近傍検索する。raw は保持しない（不変条件 9）。
    op.execute(
        f"""
        CREATE TABLE latent_index (
            entity      text        NOT NULL,
            ts          timestamptz NOT NULL,
            level       text        NOT NULL,
            wm_version  text        NOT NULL,
            vec         vector({LATENT_DIM}) NOT NULL,
            PRIMARY KEY (entity, ts, level, wm_version)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE latent_index IS "
        "'階層潜在の索引。level は fast/mid/slow。再接地はこの表を走査して行う。'"
    )
    op.execute(
        "SELECT create_hypertable('latent_index', 'ts', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
    )
    # HNSW（cosine）。level / wm_version の絞り込みを先に効かせるため複合 B-tree も持つ。
    op.execute(
        "CREATE INDEX latent_index_hnsw ON latent_index "
        "USING hnsw (vec vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX latent_index_scope_idx ON latent_index (level, wm_version, entity, ts DESC)"
    )

    # ---- メトリクス（設計書 §12.3）------------------------------------------
    # プローブ較正、予測精度、整合残差など。閾値判定は Settings 側で行う。
    op.execute(
        """
        CREATE TABLE metrics (
            ts          timestamptz NOT NULL,
            name        text        NOT NULL,
            value       double precision NOT NULL,
            site        text        NOT NULL,
            labels      jsonb       NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (name, ts, site, labels)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE metrics IS "
        "'可観測性の時系列（プローブ AUROC/ECE、予測 CRPS・被覆率、整合残差、照合の起票数など）。'"
    )
    op.execute(
        "SELECT create_hypertable('metrics', 'ts', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
    )
    op.execute("CREATE INDEX metrics_name_idx ON metrics (name, site, ts DESC)")

    # ---- 隔離キュー（設計書 §3.9, §5.7）------------------------------------
    # SHACL の sh:Violation は登録せず隔離する。捨てないのが要点（人が確認する）。
    op.execute(
        """
        CREATE TABLE quarantine (
            event_id    uuid        PRIMARY KEY,
            topic       text        NOT NULL,
            subject     text        NOT NULL,
            reason      text        NOT NULL,
            payload     jsonb       NOT NULL,
            quarantined_at timestamptz NOT NULL DEFAULT now(),
            reviewed_at timestamptz,
            resolution  text
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE quarantine IS "
        "'構造制約違反（sh:Violation）で登録を拒否したイベント。捨てずに人の確認へ回す。'"
    )
    op.execute(
        "CREATE INDEX quarantine_unreviewed_idx ON quarantine (quarantined_at) "
        "WHERE reviewed_at IS NULL"
    )


def downgrade() -> None:
    """テーブルを落とす（拡張は残す。他が使っている可能性があるため）。"""
    op.execute("DROP TABLE IF EXISTS quarantine")
    op.execute("DROP TABLE IF EXISTS metrics")
    op.execute("DROP TABLE IF EXISTS latent_index")
    op.execute("DROP TABLE IF EXISTS processed_events")
