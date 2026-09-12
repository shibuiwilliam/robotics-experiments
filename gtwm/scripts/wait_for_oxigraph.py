"""Oxigraph（http://localhost:7878）の起動を HTTP ポーリングで待つ。

docker-compose.yml の oxigraph サービスは shell/wget/curl を含まない distroless
相当のイメージのため、コンテナ側の CMD healthcheck が書けない。代わりに
`make up` からこのスクリプトを呼び、SPARQL クエリエンドポイントが応答するまで待つ。
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request

URL = "http://localhost:7878/query"
TIMEOUT_S = 30
INTERVAL_S = 0.5


def main() -> int:
    deadline = time.monotonic() + TIMEOUT_S
    req = urllib.request.Request(
        URL,
        data=b"ASK { ?s ?p ?o }",
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
        },
        method="POST",
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print("Oxigraph OK: http://localhost:7878")
                    return 0
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(INTERVAL_S)
    print(f"Oxigraph が {TIMEOUT_S}s 以内に応答しませんでした: {URL}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
