"""コネクタを HTTP で公開する薄いラッパー（ADR-0002）。標準ライブラリのみに依存する
（`http.server`）。Docker イメージを軽量・高速ビルドに保つため、torch/mujoco/rdflib 等の
重い依存を一切 import しない（`connector.py`/`policy.py`/`audit.py`/`models.py` は
pydantic 以外は標準ライブラリのみ）。

エンドポイント：
- `GET /health` -> 200 "ok"（Docker healthcheck 用）
- `POST /exchange` -> `ExchangeRequest` の JSON を受け取り `ExchangeResponse` の JSON を返す
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gtwm.dataspace.connector import Connector
from gtwm.dataspace.models import ExchangeRequest


def make_handler(connector: Connector) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # 標準の stderr アクセスログは静音化する（stdlib既定は冗長すぎる）

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/exchange":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                req = ExchangeRequest.model_validate_json(body)
            except Exception as exc:  # noqa: BLE001
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())
                return
            resp = connector.handle_request(req, now=datetime.now(UTC))
            payload = resp.model_dump_json().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(connector: Connector, port: int) -> ThreadingHTTPServer:
    """`ThreadingHTTPServer` を起動して返す（呼び出し側が起動・停止を管理する）。"""
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(connector))  # noqa: S104
    return server


if __name__ == "__main__":
    import os

    from gtwm.dataspace.audit import AuditLog
    from gtwm.dataspace.policy import OdrlPolicy

    site_id = os.environ.get("SITE_ID", "site_a")
    port = int(os.environ.get("PORT", "8000"))
    policy = OdrlPolicy(
        permitted_purposes=frozenset({"inbound_planning"}),
        retention_days=int(os.environ.get("RETENTION_DAYS", "30")),
    )
    audit = AuditLog(f"/data/{site_id}_audit.sqlite")
    conn = Connector(site_id=site_id, policy=policy, audit=audit)
    httpd = serve(conn, port)
    print(f"gtwm dataspace connector ({site_id}) listening on :{port}", flush=True)  # noqa: T201
    httpd.serve_forever()
