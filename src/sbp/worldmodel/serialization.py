"""WM の重みの直列化（Koopman。M8-24、ADR-0023）。

学習済みの Koopman モデルを**プロセスの外に出せる形**にする。これが無いと、エンコードは学習した
プロセスの寿命しか持たない（``docs/PROGRESS.md`` の既知の制約「重みの直列化とロード」）。

**形式は numpy の ``.npz``（``allow_pickle=False``）+ メタ（JSON）である。** Koopman は配列だけで
足りる。pickle は使わない——読み込みで任意コードが動くため、置き場から読む重みは信用できない
データとして扱う。メタ（モデル ID・版・チャンネル・設定・形式の版）は JSON を ``uint8``
配列に入れて同じ ``.npz`` に同梱する（重みとメタが別々に古くなる事故を防ぐ）。

**検証してから渡す。** 読めない形式の版・未学習・版が ``latest``・配列の形がチャンネル数 ×
遅れ段数と合わない・有限でない値、のどれでも失敗する（黙って別の潜在空間のモデルを返さない）。
置き場の鍵に**版が出る**（設計書 §4.8）。同じ鍵には上書きしない（``ArtifactStore.put`` の既定）。

``bridge`` のプローブの直列化（``sbp.bridge.serialization``）とは別の形式である——プローブは線形で
重みが検査材料になるので JSON にした。WM の重みは配列が大きく、人が diff を読むものではない。
"""

from __future__ import annotations

import io
import json
import re
from typing import TYPE_CHECKING, Any, Final

import numpy as np

from sbp.core.errors import WorldModelError
from sbp.core.logging import get_logger
from sbp.worldmodel.base import LatentLevel, LatentSpec, WorldModelMeta
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from sbp.knowledge.artifacts import ArtifactStore

__all__ = [
    "STATE_SCHEMA_VERSION",
    "artifact_key",
    "dumps",
    "load_model",
    "loads",
    "save_model",
]

log = get_logger(__name__)

STATE_SCHEMA_VERSION: Final = "1.0.0"
"""形式の版。**読めない版は読まない**（MAJOR が違えば拒否）。"""

_META_KEY: Final = "__meta__"
_FAMILY: Final = "koopman"


def artifact_key(model_id: str, version: str) -> str:
    """置き場の鍵（``models/<モデル>/<版>/koopman.npz``）。

    モデル ID は IRI なので、スキームを落として英数字以外を ``_`` にする。**版が鍵に出る**。

    Args:
        model_id: モデル IRI。
        version: 版（``latest`` は不可）。

    Raises:
        WorldModelError: 版が空または ``latest`` の場合。
    """
    if not version or version == "latest":
        raise WorldModelError(
            "a world model version must be concrete; 'latest' would let a different set of "
            "weights hide behind the same name"
        )
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"^[a-z]+://", "", model_id)).strip("_")
    if not slug:
        raise WorldModelError(f"cannot derive a storage key from model id {model_id!r}")
    return f"models/{slug}/{version}/{_FAMILY}.npz"


def dumps(model: KoopmanModel) -> bytes:
    """学習済みのモデルをバイト列にする。

    Args:
        model: 学習済みの Koopman モデル。

    Raises:
        WorldModelError: 未学習の場合。
    """
    arrays = model.export_state()
    meta: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "family": _FAMILY,
        "model_id": model.meta.model_id,
        "version": model.meta.version,
        "domain": model.meta.domain,
        "channels": list(model.channels),
        "training_provenance": model.meta.training_provenance,
        "config": {
            "delays": model.config.delays,
            "rank": model.config.rank,
            "ridge": model.config.ridge,
            "ensemble_size": model.config.ensemble_size,
        },
    }
    payload = {
        **arrays,
        _META_KEY: np.frombuffer(json.dumps(meta, sort_keys=True).encode("utf-8"), dtype=np.uint8),
    }
    buffer = io.BytesIO()
    np.savez(buffer, **payload)  # type: ignore[arg-type]
    return buffer.getvalue()


def loads(data: bytes) -> KoopmanModel:
    """バイト列から学習済みのモデルを復元する（検証つき）。

    Args:
        data: :func:`dumps` が作ったバイト列。

    Raises:
        WorldModelError: 形式が読めない・版が読めない・メタが欠けている・配列の形が合わない・
            有限でない値がある・pickle を含む場合。
    """
    try:
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            if _META_KEY not in archive.files:
                raise WorldModelError("world model weights carry no metadata")
            meta = json.loads(bytes(archive[_META_KEY].astype(np.uint8)).decode("utf-8"))
            state: dict[str, NDArray[np.float64]] = {
                name: np.asarray(archive[name], dtype=np.float64)
                for name in archive.files
                if name != _META_KEY
            }
    except WorldModelError:
        raise
    except (ValueError, OSError, UnicodeDecodeError, TypeError) as exc:
        # pickle を含む配列（allow_pickle=False で拒否される）・壊れた zip・JSON でないメタ
        raise WorldModelError(f"cannot read the world model weights: {exc}") from exc
    _check_meta(meta)
    config = KoopmanConfig(**meta["config"])
    channels = tuple(str(name) for name in meta["channels"])
    model_meta = WorldModelMeta(
        model_id=str(meta["model_id"]),
        version=str(meta["version"]),
        domain=str(meta["domain"]),
        modalities=channels,
        latent_spec=LatentSpec(dims={LatentLevel.MID: len(channels) * config.delays}),
        training_provenance=str(meta.get("training_provenance", "")),
    )
    return KoopmanModel.from_state(meta=model_meta, config=config, channels=channels, state=state)


def _check_meta(meta: object) -> None:
    """メタの形と形式の版を検査する。"""
    if not isinstance(meta, dict):
        raise WorldModelError("world model metadata is not an object")
    version = str(meta.get("schema_version", ""))
    if version.split(".", maxsplit=1)[0] != STATE_SCHEMA_VERSION.split(".")[0]:
        raise WorldModelError(
            f"world model weights use format {version!r}; this build reads {STATE_SCHEMA_VERSION} "
            "(an unknown format is not guessed)"
        )
    if meta.get("family") != _FAMILY:
        raise WorldModelError(f"expected a {_FAMILY} model, got {meta.get('family')!r}")
    for field in ("model_id", "version", "domain", "channels", "config"):
        if field not in meta:
            raise WorldModelError(f"world model metadata is missing '{field}'")
    if not isinstance(meta["config"], dict) or not isinstance(meta["channels"], list):
        raise WorldModelError("world model metadata has the wrong shape")


def save_model(store: ArtifactStore, model: KoopmanModel) -> str:
    """モデルを置き場に置く（**既存の鍵には上書きしない**）。

    Args:
        store: 置き場。
        model: 学習済みのモデル。

    Returns:
        置いた鍵。

    Raises:
        WorldModelError: 未学習の場合。
        ArtifactImmutableError: 同じ版が既に置かれている場合（版を上げる）。
    """
    key = artifact_key(model.meta.model_id, model.meta.version)
    store.put(key, dumps(model))
    log.info("worldmodel.weights_saved", key=key, wm_version=model.meta.version_iri)
    return key


def load_model(store: ArtifactStore, *, model_id: str, version: str) -> KoopmanModel:
    """置き場からモデルを読む。**鍵の版と、中身のモデル ID・版が一致することを検査する。**

    Args:
        store: 置き場。
        model_id: モデル IRI。
        version: 版（``latest`` は不可）。

    Raises:
        WorldModelError: 中身が鍵と食い違う、または読めない場合。
        ArtifactNotFoundError: 鍵が存在しない場合。
    """
    key = artifact_key(model_id, version)
    model = loads(store.get(key))
    if model.meta.model_id != model_id or model.meta.version != version:
        raise WorldModelError(
            f"weights at {key} belong to {model.meta.version_iri}, not {model_id}/{version}"
        )
    return model
