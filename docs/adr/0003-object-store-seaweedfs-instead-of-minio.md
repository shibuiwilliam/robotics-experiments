# ADR-0003: オブジェクトストアを MinIO から SeaweedFS に差し替える

- 日付: 2026-10-03
- 状態: 採択
- 触る層: infra（+ 将来 knowledge の raw 層アクセス、worldmodel のモデル重み保存）

## 文脈

`CLAUDE.md` のインフラ表と設計書 §6.2 は raw 層（センサ生値）とモデル重みの保管に MinIO（S3 互換）を指定している。M0-08（`infra/compose.yaml`）の実装中に、**MinIO の公開コンテナイメージが取得できないことが判明した**。

```
$ docker compose up -d
Error response from daemon: pull access denied for minio/minio,
repository does not exist or may require 'docker login'
```

調査結果（2026-10-03 時点）。最初の調査は存在しないタグと `latest` だけを見ていたため不十分だった。レジストリ API に直接問い合わせて確定させた結果は次のとおり。

| 取得経路 | 確認方法 | 結果 |
|---|---|---|
| `minio/minio`（Docker Hub） | registry v2 `tags/list` に匿名トークンで問い合わせ | `UNAUTHORIZED: authentication required` |
| `minio/minio`（Docker Hub） | Hub API `repositories/minio/minio/tags` | `object not found`（匿名には存在しない） |
| `quay.io/minio/minio` | Quay API `repository/minio/minio` | `Requires authentication` |
| `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | registry v2 `manifests/<tag>` に匿名アクセス | **HTTP 401** |
| `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` | `docker pull`（匿名） | `unauthorized: access to the requested resource is not authorized` |

タグを明示しても両レジストリで 401 が返るため、**MinIO は匿名での取得経路を失っている**と判断できる。なお本開発機には別プロジェクトが過去に取得した MinIO イメージがローカルに残っているが、これは再現性の根拠にならない（新規環境では取得できない）。

`make bootstrap` を実行した開発者がレジストリ認証なしに環境を再現できないため、ゲート G0 の条件「`make bootstrap` から新規環境で 30 分以内に再現できる」を満たせない。

CLAUDE.md は「便利だからと新サービスを増やす」ことを禁じているが、これは**サービスの追加ではなく既存サービスの置換**であり、S3 互換 API という契約は変えない。ストア選定は ADR 必須（CLAUDE.md Git/PR/ADR 節）のため本 ADR を起票する。

## 選択肢

いずれも arm64 対応と `mem_limit: 1g` 以内での動作を条件とした。

1. **SeaweedFS（`chrislusf/seaweedfs`）** — S3 ゲートウェイを内蔵する分散ファイルシステム。
   - 利点: 匿名 pull 可能（arm64 マニフェストあり）。単一コンテナで `weed server -s3` により S3 API が立つ。永続ボリュームに対応。実績が長い。
   - 欠点: 本来は分散 FS であり、単一ノード用途としては機能が過剰。S3 API の実装範囲は MinIO より狭い（ただし SBP が使うのは PutObject / GetObject / ListObjects / バケット作成のみ）。
   - MacBook 上のコスト: メモリ数百 MB、起動数秒。
2. **Garage（`dxflrs/garage`）** — 自己ホスト向けの軽量 S3 互換ストア。
   - 利点: 匿名 pull 可能。メモリ消費が最小。S3 互換に専念している。
   - 欠点: 起動に `garage.toml` と `garage layout assign` によるレイアウト初期化が必要で、`make seed` の手順が増える。単一ノードでもクラスタ概念が露出する。
3. **adobe/s3mock** — テスト用の S3 モック。
   - 利点: 最軽量、設定不要。
   - 欠点: **インメモリで永続しない。** raw 層は「再潜在化と監査のために 30〜90 日保持」（設計書 §6.2）が要件であり、再接地（M7-01）はモデル重みの版を跨いで参照する。永続しないストアはこの要件を満たさない。
4. Docker Hub 認証を前提にして MinIO を使い続ける — 開発者ごとに `docker login` と MinIO 側のアカウントが必要になり、`make bootstrap` の再現性が落ちる。CI（X-01）でも秘密情報の設定が増える。棄却。

## 決定

**選択肢 1（SeaweedFS）を採る。** イメージは `chrislusf/seaweedfs:3.80` に固定する。

```yaml
objectstore:
  image: chrislusf/seaweedfs:3.80
  command: ["server", "-dir=/data", "-s3", "-s3.port=9000", "-master.volumeSizeLimitMB=256"]
```

理由:

- 永続性（選択肢 3 を外す決定要因）と匿名取得（選択肢 4 を外す決定要因）を同時に満たすものが 1 と 2。
- 1 と 2 の差は初期化の複雑さ。Garage のレイアウト初期化は `make seed` に不可逆な手順を増やし、`make nuke` 後の再構築手順も増える。SeaweedFS は単一コマンドで S3 API が立ち、バケット作成だけが `make seed` の責務として残る。これは MinIO のときと同じ形であり、**移行コストが最小**。
- S3 互換 API という抽象は維持されるため、SBP のコード側（`boto3` 互換クライアント経由のアクセス）は変更不要。将来 MinIO の取得経路が回復した場合、`compose.yaml` のイメージと command の差し替えだけで戻せる。

サービス名は compose 上で `objectstore` とする（実装を名前で固定しないため。`minio` という名前を残すと実装が変わったときに嘘になる）。

## 影響

- 不変条件への影響: なし。不変条件 9（生映像・音声を永続化しない）は変わらず、raw 層に入れるのは合成データと潜在のみ（開発中）。
- オントロジー版: 影響なし。再接地不要。
- 設定への影響: `.env.example` の `SBP_S3_*` はそのまま使える（S3 互換 API のため）。`SBP_PORT_MINIO` → `SBP_PORT_S3` に改名する。
- `CLAUDE.md` のインフラ表との差異: 表の「minio」行を「objectstore（SeaweedFS）」に更新する必要がある。CLAUDE.md は規約の正典なので、本 ADR の採択と同時に更新する。
- テスト・運用への影響: 統合テストのオブジェクトストア疎通確認は S3 API 経由で行い、実装に依存しない。`make seed` のバケット作成は S3 API（`CreateBucket`）で行う。
- サービス数は 4 のまま（置換であり追加ではない）。メモリ上限も 1 GB のまま。

## 未決

MinIO の匿名取得経路が回復した場合に戻すかどうか。戻す利点は CLAUDE.md の原文との一致だけであり、S3 互換の抽象が保たれている限り実利はない。**再評価は M7（運用）の時点で行う**ものとし、それまでは SeaweedFS を既定とする。
