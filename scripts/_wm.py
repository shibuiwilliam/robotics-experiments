"""エンコードジョブと WM 学習の共通部品（M8-24、ADR-0023）。"""

from __future__ import annotations

from pathlib import Path

from sbp.core.config import Settings
from sbp.core.errors import ConfigError

__all__ = ["split_wm_version", "spool_path"]


def spool_path(settings: Settings, override: Path | None = None) -> Path:
    """スプールの置き場。**引数か ``SBP_WM_SPOOL_DIR`` が無ければ失敗する**（既定値を置かない）。

    Args:
        settings: 設定。
        override: コマンドライン引数。

    Raises:
        ConfigError: 置き場が決まらない、または存在しない場合。
    """
    raw = str(override) if override is not None else settings.worldmodel.spool_dir
    if not raw:
        raise ConfigError(
            "no spool directory: pass --spool or set SBP_WM_SPOOL_DIR; a default here would "
            "silently read an empty place and report zero windows as healthy"
        )
    path = Path(raw)
    if not path.exists():
        raise ConfigError(f"spool path does not exist: {path}")
    return path


def split_wm_version(wm_version: str) -> tuple[str, str]:
    """``SBP_BRIDGE_WM_VERSION``（``<モデル IRI>/<版>``）をモデル ID と版に分ける。

    Args:
        wm_version: WM 版 IRI。

    Raises:
        ConfigError: 空、または版が読み取れない場合。
    """
    model_id, _, version = wm_version.rpartition("/")
    if not wm_version or not model_id or not version:
        raise ConfigError(
            "SBP_BRIDGE_WM_VERSION must be '<model iri>/<version>' "
            f"(e.g. https://sbp.example/wm/koopman/0.1.0), got {wm_version!r}"
        )
    return model_id, version
