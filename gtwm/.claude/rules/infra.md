---
paths:
  - "docker/**"
  - "docker-compose*.yml"
  - "Makefile"
  - "pyproject.toml"
  - ".pre-commit-config.yaml"
  - ".github/**"
---

# インフラ（Docker、Makefile、依存関係）

## Apple Silicon での Docker
- 全サービスに arm64 イメージを使う。`platform: linux/amd64` を書く場合は理由をコメントに残し、`docs/status.md` に列挙する。
- ボリュームは `./.data/<service>/`（gitignore）。ポートは固定：Oxigraph 7878、TimescaleDB 5432、MinIO 9000/9001、Grafana 3000、MLflow 5000、Streamlit 8501、WMS モック 8080、EDC（profile p2）19191 以降。
- 全サービスに `healthcheck` を付け、`make up` は `--wait` で起動完了を待つ。
- 既定は profile `core`（oxigraph, timescaledb, minio, grafana）。`p2` プロファイル（EDC コネクタ×2、拠点ごとに別ネットワーク `site_a` `site_b`）は P2 相当のタスクに入るまで作らない。
- サービスの追加・削除は事前に相談する。まず「なくても回るか」を検討する（既定はインプロセス／SQLite）。

## Makefile
- CLAUDE.md の「コマンド」節と1対1に対応させる。ターゲットを増やしたら CLAUDE.md も更新する。
- 全ターゲットは `uv run` 経由で実行し、グローバル Python を使わない。
- 環境変数は `.env` を `include`/`export` で読み込み、`PYTORCH_ENABLE_MPS_FALLBACK=1` `TOKENIZERS_PARALLELISM=false` `MUJOCO_GL=glfw`（macOS 既定）を必ず設定する。
- `make test-sim` は `-p no:xdist` で直列実行。`make test` は `-n auto` 可。

## 依存関係（pyproject）
- Python `>=3.11,<3.12`。optional-dependencies：`sim`（mujoco, imageio, imageio-ffmpeg, opencv-python-headless）、`ml`（torch, torchvision, transformers, einops, scipy, scikit-learn, hdbscan）、`kg`（rdflib, pyshacl, pyoxigraph, lark）、`llm`（anthropic, openai, google-genai）、`ui`（streamlit, plotly）、`dev`（pytest, pytest-xdist, ruff, mypy, pre-commit, mlflow, pandas, pyarrow）。
- `uv.lock` をコミットする。バージョン更新は `chore(deps):` で単独コミット。
- ネイティブビルドが必要なパッケージ（Scallop 等）は optional の `experimental` に隔離し、失敗しても他が動くようにする。
- `brew` 依存は `uv` と `ffmpeg` の2つだけに留める。増やす時は `make setup` の検査と CLAUDE.md を更新する。

## pre-commit / CI
- pre-commit：ruff（check + format）、mypy（src）、`gtwm kg validate`（ontology/ の変更時のみ）、大きなファイル（>5MB）のコミット禁止。
- GitHub Actions を使う場合は `macos-14` ランナーで `make lint test` のみ。sim / integration テストは CI で回さない。
