---
paths:
  - "infra/**"
  - "tests/**"
  - "services/**"
  - "Makefile"
  - "pyproject.toml"
  - ".env.example"
---
# インフラ / テストのルール

## Compose（`infra/compose.yaml`）
- 既定サービスは redpanda, postgres（timescaledb-ha）, oxigraph, objectstore（SeaweedFS。ADR-0003）の 4 つ。profile は `ui`, `observability`, `console`。
- 全サービスに `platform: ${SBP_PLATFORM:-linux/arm64}`、`healthcheck`、`mem_limit` を付ける。合計 6 GB 以内。
  - 既知の例外: oxigraph は distroless でコンテナ内に検査手段が無いため healthcheck を付けない。疎通検査は統合テストの fixture とワーカー起動時に行う（ADR-0004）。
- ボリュームは名前付き（`sbp_pg`, `sbp_rp`, `sbp_ox`, `sbp_s3`）。削除は `make nuke` のみ。
- ポートは 5432, 9092, 7878, 9000 / 9001, 8000（API）。衝突時は `.env` で上書きする。

## 設定
- `sbp.core.config.Settings`（pydantic-settings、`SBP_` プレフィクス）。環境変数を追加したら `.env.example` を同じコミットで更新する。

## テスト（pytest）
- マーカー: `unit`（既定）, `integration`（Compose 必須）, `slow`, `llm_live`。`make test` は `-m "not integration and not llm_live"`。
- 統合テストは `tests/integration/conftest.py` の fixture で Compose の疎通を確認し、未起動なら skip ではなく明示エラーにする。
- 合成データは `sbp.testing.synth.SynthWorld(seed)`。真値は `SynthWorld.oracle` で、テストコード内でのみ参照する。
- 性質テスト（hypothesis）は `tests/property/`。二時制・様相格子・区間代数・冪等性。
- 時刻は `freezegun` で固定、乱数は seed 固定、ネットワークは `pytest-socket` で遮断。
- LLM は `replay` のみ。`llm_live` は CI で走らせない。

## Makefile
- 全ターゲットに `.PHONY` と 1 行説明（`make help` で一覧）。Python は必ず `uv run` 経由。

## ワーカー（`services/`）
- 1 ワーカー 1 プロセス。SIGTERM で graceful shutdown し、オフセットをコミットしてから終了する。起動時に Settings と Compose の疎通を検査する。
