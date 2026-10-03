"""潜在索引を次元ごとに分け、WM 版 → 表の対応を登録表で持つ。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

設計書 §6.5、`.claude/rules/knowledge.md`「次元 D は ``wm_version`` ごとに固定。次元が変わる
WM 更新は新テーブルを作り、移行は再接地ジョブで行う」に対応する。

**なぜ登録表を置くのか。** 0001 は ``latent_index`` 1 本（``vector(64)``、SynthWorld の合成潜在に
合わせた次元）だった。M4 で実モデルを入れると、koopman は 24 次元、jepa_ts は 16 次元になり、
同じ表に入らない（pgvector は列ごとに次元が固定）。規則どおり次元ごとに表を分けるが、
**どの版がどの表を使うかを命名規約で表すのは脆い**。``latent_index_v2`` という名前からは
次元が読めず、運用で表を取り違える。そのため ``latent_index_registry`` に
``wm_version → (表名, 次元)`` を持ち、アプリは登録表を引いてから書き込む。次元の食い違いは
INSERT 前に落ちる。

表の名前自体は ``latent_index_d<次元>`` にした（規則の ``v<N>`` から逸れるが、運用者が名前を
見て次元を判断できるほうが事故が少ない。版と表の対応は登録表が持つので命名は補助情報である）。
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# M4 のモデル族が実際に使う次元。族の設定（遅れ段数・潜在次元）を変えて新しい次元が必要に
# なったら、ここに足して新しいマイグレーションを切る（既存の表は消さない——過去の潜在が
# 入っており、再接地ジョブがそれを読む）。
DIMENSIONS = (24, 16)


def upgrade() -> None:
    """登録表と次元別の潜在索引を作る。"""
    op.execute(
        """
        CREATE TABLE latent_index_registry (
            wm_version  text        NOT NULL PRIMARY KEY,
            table_name  text        NOT NULL,
            dim         integer     NOT NULL CHECK (dim > 0),
            registered_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE latent_index_registry IS "
        "'WM 版 → 潜在索引の表と次元。アプリはここを引いてから書き込む（次元の取り違え防止）。'"
    )
    # 0001 が作った既定の表（64 次元、合成潜在）も登録しておく。登録表にあるものだけを
    # 正当な書き込み先にするため、既存の表を例外扱いにしない。
    op.execute(
        """
        INSERT INTO latent_index_registry (wm_version, table_name, dim)
        VALUES ('https://sbp.example/model/synth/1.0.0', 'latent_index', 64)
        """
    )

    for dim in DIMENSIONS:
        table = f"latent_index_d{dim}"
        op.execute(
            f"""
            CREATE TABLE {table} (
                entity      text        NOT NULL,
                ts          timestamptz NOT NULL,
                level       text        NOT NULL,
                wm_version  text        NOT NULL,
                vec         vector({dim}) NOT NULL,
                PRIMARY KEY (entity, ts, level, wm_version)
            )
            """
        )
        op.execute(
            f"COMMENT ON TABLE {table} IS "
            f"'{dim} 次元の潜在索引。どの WM 版がここを使うかは latent_index_registry が持つ。'"
        )
        op.execute(
            f"SELECT create_hypertable('{table}', 'ts', "
            "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
        )
        # HNSW（cosine）。level / wm_version の絞り込みを先に効かせるため複合 B-tree も持つ。
        op.execute(
            f"CREATE INDEX {table}_hnsw ON {table} "
            "USING hnsw (vec vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
        op.execute(
            f"CREATE INDEX {table}_scope_idx ON {table} (level, wm_version, entity, ts DESC)"
        )


def downgrade() -> None:
    """次元別の表と登録表を落とす。"""
    for dim in DIMENSIONS:
        op.execute(f"DROP TABLE IF EXISTS latent_index_d{dim}")
    op.execute("DROP TABLE IF EXISTS latent_index_registry")
