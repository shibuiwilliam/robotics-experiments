# 拠点コネクタ（ADR-0002：最小 HTTP コネクタ）。
# gtwm.dataspace.* は pydantic 以外は標準ライブラリのみに依存するため、torch/mujoco/rdflib
# などの重い extras は一切インストールしない（ビルドを高速・軽量に保つ）。
FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
# hatchling は pyproject.toml の readme (docs/poc_plan.md) の実在を要求する（内容自体は
# 使わない）ため、メタデータ生成のためだけにこの1ファイルをコピーする。
COPY docs/poc_plan.md docs/poc_plan.md
COPY src/gtwm/__init__.py src/gtwm/__init__.py
COPY src/gtwm/dataspace/ src/gtwm/dataspace/

# pyproject.toml の [project.dependencies]（pydantic 等、extras 抜き）だけをインストールする。
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["python3", "-m", "gtwm.dataspace.server"]
