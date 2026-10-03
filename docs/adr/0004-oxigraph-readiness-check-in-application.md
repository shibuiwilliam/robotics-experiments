# ADR-0004: Oxigraph の疎通検査をコンテナの healthcheck ではなくアプリ側で行う

- 日付: 2026-10-03
- 状態: 採択
- 触る層: infra, knowledge（疎通検査の実装位置）, tests

## 文脈

`.claude/rules/infra-testing.md` は「全サービスに `platform: ${SBP_PLATFORM:-linux/arm64}`、`healthcheck`、`mem_limit` を付ける」と定めている。M0-08 の実装中に、**Oxigraph の公式イメージではコンテナ内から healthcheck を実行できない**ことが判明した。

`oxigraph/oxigraph:0.5.11` は distroless であり、確認した事実は次のとおり。

| 試したこと | 結果 |
|---|---|
| `docker exec sbp-oxigraph sh -c ...` | `exec: "sh": executable file not found in $PATH` |
| `docker run --entrypoint /bin/ls` | `stat /bin/ls: no such file or directory` |
| イメージの構成 | Entrypoint は `/usr/local/bin/oxigraph` のみ。シェル・wget・curl・timeout なし |
| `oxigraph query --help` | `--location <LOCATION>` が必須。HTTP エンドポイントを指定する方法がない |

したがって `CMD-SHELL` 形式は使えず、`CMD` 形式で使える唯一のバイナリ（`oxigraph`）も HTTP 疎通を確認できない。`oxigraph query --location /data` は稼働中のサーバと同じストアを開くため、RocksDB のロックで競合する（検査が本体を壊す）。

HTTP エンドポイント自体は正常で、ホストから `GET /query?query=SELECT (1 AS ?x) WHERE {}` は HTTP 200 を返す。問題は「コンテナ内に検査の実行手段が無い」ことだけである。

## 選択肢

1. **healthcheck を付けず、疎通検査をアプリ側（統合テストの fixture とワーカー起動時）に置く。**
   - 利点: 検査が「SPARQL クエリが実際に通る」ことを確認する。これは本来必要な検査と同一であり、`.claude/rules/infra-testing.md` が別途要求している「統合テストは Compose の疎通を確認し、未起動なら skip ではなく明示エラー」「ワーカーは起動時に Settings と Compose の疎通を検査する」とも重なる。
   - 欠点: `docker compose ps` の Health 列が空欄になる。`depends_on: condition: service_healthy` が使えない。
2. 形だけの healthcheck（`["CMD", "/usr/local/bin/oxigraph", "--version"]`）を付ける。
   - 利点: ルールの字面を満たす。Health 列が埋まる。
   - 欠点: **バイナリが実行できることしか確認しない。** サーバが落ちていても healthy になる。「検査が通ったのに動かない」は healthcheck が無い状態より悪い（偽の安心を与える）。棄却。
3. シェル入りの非公式イメージまたは自前ビルドに差し替える。
   - 欠点: 公式配布から離れ、供給元の信頼性と更新追随のコストが増える。healthcheck 1 つのために払う代償として大きい。棄却。
4. 専用の healthcheck サイドカーを追加する。
   - 欠点: サービス追加に当たる（CLAUDE.md が禁じる方向）。4 サービス制約も崩れる。棄却。

## 決定

**選択肢 1 を採る。** Oxigraph にだけ `healthcheck` を付けず、`infra/compose.yaml` にその理由をコメントで明記する。疎通検査は次の 2 箇所に置く。

1. `tests/integration/conftest.py` の fixture — SPARQL の `ASK {}` 相当で疎通を確認し、未起動なら skip せず明示的に失敗させる。
2. ワーカー・API の起動時検査 — `sbp.knowledge` のクライアント初期化時に 1 回クエリを投げる。

`depends_on` で `service_healthy` を使えない点は、上記のアプリ側リトライで吸収する（接続が確立するまで短い間隔で再試行し、上限を超えたら `DependencyUnavailableError`）。

残りの 3 サービス（redpanda / postgres / objectstore）には healthcheck を付けており、ルールの意図（`make up` の後に「使える状態か」が機械的に分かる）は 4 分の 3 で満たされる。

## 影響

- 不変条件への影響: なし。
- オントロジー版: 影響なし。
- `.claude/rules/infra-testing.md` との差異: 「全サービスに healthcheck」という記述に対する既知の例外を 1 件作る。ルール側にこの例外と ADR への参照を追記する。
- テストへの影響: `tests/integration/conftest.py` に Oxigraph の疎通検査を必ず置く（これが実質の healthcheck になるため、省略すると検査が完全に消える）。M0-13 の受け入れ条件に含める。
- 運用への影響: `docs/runbooks/local-setup.md` に「oxigraph の Health 列は空欄が正常」と記載する。空欄を見た運用者が異常と誤認するのを防ぐ。

## 未決

Oxigraph の将来のイメージにシェルまたは HTTP 疎通用のサブコマンドが入った場合は、選択肢 1 から通常の healthcheck に戻す。再評価は M7（運用）で行う。
