"""モデル成果物の置き場（M8-01）。

重み・プローブ束ねのような**バイト列の成果物**を置く場所。知識グラフ（主張）とも潜在索引
（ベクトル）とも別の層で、設計書 §4.8 が ``s3://models/<model_id>/<version>/`` と定める
置き場がこれに当たる（ADR-0003 により実体は SeaweedFS）。

**ここはバイト列しか知らない。** プローブ束ねの形式は :mod:`sbp.bridge.serialization` が
持ち、``bridge`` は置き場を知らない（import-linter の契約「bridge は永続化しない」）。
両者を繋ぐのはワーカーである——潜在索引の読み出しが ``services`` 側にあるのと同じ形。

**既定で上書きを拒否する。** 成果物は版ごとに不変である。同じキーに別の重みを入れられると、
「``latest`` を禁じているのに実質 ``latest`` になる」——過去の信念が「この版のこの重みで
出した」と記録していても、その重みが入れ替わっていれば説明は再構成できない（設計書 §8.8）。
上書きが必要な場面（壊れた成果物の修復）は ``overwrite=True`` を明示して呼ぶ。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urlparse

from sbp.core.errors import ArtifactImmutableError, ArtifactNotFoundError, KnowledgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from sbp.core.config import Settings

__all__ = [
    "ArtifactStore",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "parse_s3_uri",
]

log = get_logger(__name__)


@runtime_checkable
class ArtifactStore(Protocol):
    """成果物の置き場の抽象。キー（相対パス）→ バイト列。

    単体テストを Compose 無しで回すために Protocol にしている。実運用は
    :class:`S3ArtifactStore`、テストと開発機は :class:`LocalArtifactStore`。
    """

    def get(self, key: str) -> bytes:
        """成果物を読む。

        Args:
            key: 置き場のキー（``probes/Running/1.0.0/probe.json`` など）。

        Raises:
            ArtifactNotFoundError: キーが存在しない場合。
        """
        ...

    def put(self, key: str, data: bytes, *, overwrite: bool = False) -> None:
        """成果物を置く。

        Args:
            key: 置き場のキー。
            data: 内容。
            overwrite: 既存のキーを上書きするか。既定は拒否。

        Raises:
            ArtifactImmutableError: 既存のキーを ``overwrite=False`` で置こうとした場合。
        """
        ...

    def exists(self, key: str) -> bool:
        """キーが存在するか。

        Args:
            key: 置き場のキー。
        """
        ...

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        """接頭辞に一致するキーを返す（辞書順）。

        Args:
            prefix: キーの接頭辞（``probes/`` など）。
        """
        ...


def _reject_unsafe_key(key: str) -> str:
    """キーが置き場の外を指していないか検査する。

    ``..`` と絶対パスを拒否する。成果物のキーは外部（オントロジーの IRI、設定）から来る
    ため、そのままパスに繋ぐと置き場の外を読み書きできる。

    Args:
        key: 置き場のキー。

    Returns:
        正規化したキー。

    Raises:
        KnowledgeError: キーが空、絶対パス、または ``..`` を含む場合。
    """
    if not key or key != key.strip():
        raise KnowledgeError(f"artifact key {key!r} is empty or has surrounding whitespace")
    parts = key.split("/")
    if key.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise KnowledgeError(
            f"artifact key {key!r} must be a relative path without '.' or '..' segments; "
            "keys come from IRIs and settings and must not escape the store"
        )
    return key


class LocalArtifactStore:
    """ファイルシステム上の置き場（テストと開発機）。

    Attributes:
        root: 置き場の根ディレクトリ。
    """

    def __init__(self, root: Path) -> None:
        """Args:
        root: 置き場の根ディレクトリ（無ければ作る）。
        """
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """キーをパスに変える。

        Args:
            key: 置き場のキー。
        """
        return self.root / _reject_unsafe_key(key)

    def get(self, key: str) -> bytes:
        """成果物を読む。

        Args:
            key: 置き場のキー。

        Raises:
            ArtifactNotFoundError: キーが存在しない場合。
        """
        path = self._path(key)
        if not path.is_file():
            raise ArtifactNotFoundError(f"artifact {key!r} is not in {self.root}")
        return path.read_bytes()

    def put(self, key: str, data: bytes, *, overwrite: bool = False) -> None:
        """成果物を置く。

        Args:
            key: 置き場のキー。
            data: 内容。
            overwrite: 既存のキーを上書きするか。

        Raises:
            ArtifactImmutableError: 既存のキーを ``overwrite=False`` で置こうとした場合。
        """
        path = self._path(key)
        if path.exists() and not overwrite:
            raise ArtifactImmutableError(
                f"artifact {key!r} already exists; artifacts are immutable per version so "
                "that a past belief's explanation stays reconstructible (design §8.8). "
                "Pass overwrite=True only to repair a corrupted artifact."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("knowledge.artifact_put", key=key, bytes=len(data), store="local")

    def exists(self, key: str) -> bool:
        """キーが存在するか。

        Args:
            key: 置き場のキー。
        """
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        """接頭辞に一致するキーを返す（辞書順）。

        Args:
            prefix: キーの接頭辞。
        """
        found: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                found.append(key)
        return tuple(found)


class S3ArtifactStore:
    """S3 互換の置き場（ADR-0003 により SeaweedFS）。

    ``boto3`` は遅延 import する——単体テストはこのクラスを使わず、import だけで
    ネットワーク系のライブラリを読み込むのを避ける。

    Attributes:
        bucket: バケット名。
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        """Args:
        bucket: バケット名。
        endpoint: S3 エンドポイント URL。
        access_key: アクセスキー。
        secret_key: シークレットキー。**ログに出さない**（不変条件 12）。
        """
        import boto3  # noqa: PLC0415 — 遅延 import（単体テストで読み込まない）

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, bucket: str | None = None) -> S3ArtifactStore:
        """設定から組み立てる。

        Args:
            settings: 設定。
            bucket: バケット名（既定は ``s3_bucket_models``）。
        """
        return cls(
            bucket=bucket or settings.s3_bucket_models,
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )

    def get(self, key: str) -> bytes:
        """成果物を読む。

        Args:
            key: 置き場のキー。

        Raises:
            ArtifactNotFoundError: キーが存在しない場合。
        """
        safe = _reject_unsafe_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=safe)
        except self._client.exceptions.NoSuchKey as exc:
            raise ArtifactNotFoundError(f"artifact {key!r} is not in s3://{self.bucket}") from exc
        body: bytes = response["Body"].read()
        return body

    def put(self, key: str, data: bytes, *, overwrite: bool = False) -> None:
        """成果物を置く。

        Args:
            key: 置き場のキー。
            data: 内容。
            overwrite: 既存のキーを上書きするか。

        Raises:
            ArtifactImmutableError: 既存のキーを ``overwrite=False`` で置こうとした場合。
        """
        safe = _reject_unsafe_key(key)
        if not overwrite and self.exists(safe):
            raise ArtifactImmutableError(
                f"artifact {key!r} already exists in s3://{self.bucket}; artifacts are "
                "immutable per version (design §8.8). Pass overwrite=True only to repair."
            )
        self._client.put_object(Bucket=self.bucket, Key=safe, Body=data)
        log.info("knowledge.artifact_put", key=safe, bytes=len(data), store="s3")

    def exists(self, key: str) -> bool:
        """キーが存在するか。

        Args:
            key: 置き場のキー。
        """
        safe = _reject_unsafe_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=safe)
        except self._client.exceptions.ClientError:
            # head_object は存在しないキーに対して 404 の ClientError を返す（NoSuchKey ではない）
            return False
        return True

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        """接頭辞に一致するキーを返す（辞書順）。

        Args:
            prefix: キーの接頭辞。
        """
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(str(item["Key"]) for item in page.get("Contents", ()))
        return tuple(sorted(keys))


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/key`` を ``(bucket, key)`` に分ける。

    レジストリが持つ ``weights_uri`` を置き場の呼び出しに繋ぐために使う。

    Args:
        uri: ``s3://`` で始まる URI。

    Returns:
        ``(バケット, キー)``。

    Raises:
        KnowledgeError: ``s3://`` でない、またはキーが空の場合。
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise KnowledgeError(f"not an s3 URI: {uri!r} (expected s3://bucket/key)")
    key = parsed.path.lstrip("/")
    if not key:
        raise KnowledgeError(f"s3 URI {uri!r} has no key part")
    return parsed.netloc, _reject_unsafe_key(key)
