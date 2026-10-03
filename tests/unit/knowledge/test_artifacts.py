"""モデル成果物の置き場の単体テスト（M8-01）。

重点:

1. **既定で上書きしない** — 同じキーの中身が入れ替わると、``latest`` を禁じていても実質
   ``latest`` になる。過去の信念が「この版のこの重みで出した」と記録していても、重みが
   入れ替わっていれば説明は再構成できない（設計書 §8.8）。
2. **置き場の外を指すキーを拒否する** — キーは IRI と設定から来るので、``..`` を通すと
   置き場の外を読み書きできる。
3. **無いものは例外** — 「無い」を空のバイト列で返すと、呼び出し側は空の束ねを読む。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sbp.core.errors import ArtifactImmutableError, ArtifactNotFoundError, KnowledgeError
from sbp.knowledge.artifacts import ArtifactStore, LocalArtifactStore, parse_s3_uri

if TYPE_CHECKING:
    from pathlib import Path

KEY = "probes/Running/1.0.0/probe.json"


def test_local_store_satisfies_the_protocol(tmp_path: Path) -> None:
    """``LocalArtifactStore`` が :class:`ArtifactStore` として使える。"""
    store = LocalArtifactStore(tmp_path)
    assert isinstance(store, ArtifactStore)


def test_put_then_get_roundtrips(tmp_path: Path) -> None:
    """置いたバイト列がそのまま返る。"""
    store = LocalArtifactStore(tmp_path)
    store.put(KEY, b'{"schema_version": "1.0.0"}')

    assert store.get(KEY) == b'{"schema_version": "1.0.0"}'
    assert store.exists(KEY)


def test_put_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    """既存のキーは既定で上書きできない（成果物は版ごとに不変）。"""
    store = LocalArtifactStore(tmp_path)
    store.put(KEY, b"first")

    with pytest.raises(ArtifactImmutableError, match="immutable per version"):
        store.put(KEY, b"second")

    assert store.get(KEY) == b"first", "拒否されたのに中身が変わってはいけない"


def test_put_overwrites_when_explicitly_asked(tmp_path: Path) -> None:
    """``overwrite=True`` を明示すれば上書きできる（壊れた成果物の修復）。"""
    store = LocalArtifactStore(tmp_path)
    store.put(KEY, b"first")
    store.put(KEY, b"second", overwrite=True)

    assert store.get(KEY) == b"second"


def test_get_missing_key_raises(tmp_path: Path) -> None:
    """無いキーは例外になる（空のバイト列を返さない）。"""
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError, match="not in"):
        store.get(KEY)
    assert not store.exists(KEY)


@pytest.mark.parametrize(
    "key",
    [
        "../outside.json",
        "probes/../../outside.json",
        "/absolute/path.json",
        "probes//double.json",
        "probes/./here.json",
        "",
        " probes/x.json",
    ],
)
def test_unsafe_keys_are_refused(tmp_path: Path, key: str) -> None:
    """置き場の外を指すキー・空のキーを拒否する。"""
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(KnowledgeError):
        store.put(key, b"x")
    with pytest.raises(KnowledgeError):
        store.get(key)


def test_list_keys_filters_by_prefix_and_sorts(tmp_path: Path) -> None:
    """接頭辞で絞って辞書順に返す（版の一覧を取るのに使う）。"""
    store = LocalArtifactStore(tmp_path)
    store.put("probes/Running/2.0.0/probe.json", b"b")
    store.put("probes/Running/1.0.0/probe.json", b"a")
    store.put("models/koopman/0.1.0/weights.json", b"c")

    assert store.list_keys("probes/") == (
        "probes/Running/1.0.0/probe.json",
        "probes/Running/2.0.0/probe.json",
    )
    assert store.list_keys("models/") == ("models/koopman/0.1.0/weights.json",)
    assert store.list_keys("nothing/") == ()


def test_store_root_is_created(tmp_path: Path) -> None:
    """根ディレクトリが無ければ作る（開発機での最初の起動）。"""
    root = tmp_path / "deep" / "store"
    LocalArtifactStore(root)
    assert root.is_dir()


def test_parse_s3_uri() -> None:
    """``s3://bucket/key`` を分解する（レジストリの ``weights_uri`` を繋ぐ）。"""
    assert parse_s3_uri("s3://models/probes/Running/1.0.0/probe.json") == (
        "models",
        "probes/Running/1.0.0/probe.json",
    )


@pytest.mark.parametrize(
    "uri",
    [
        "http://models/x.json",
        "s3://models",
        "s3:///key.json",
        "s3://models/../escape.json",
        "models/x.json",
    ],
)
def test_parse_s3_uri_rejects_malformed(uri: str) -> None:
    """``s3://`` でない・キーが無い・置き場の外を指す URI を拒否する。"""
    with pytest.raises(KnowledgeError):
        parse_s3_uri(uri)
