"""``make dev`` の起動スクリプトの単体テスト（M8-13）。

uvicorn を起こさずに確かめられるところ（引数の解釈とワーカー名の検査）だけを見る。

重点:

1. **未知のワーカー名を黙って飛ばさない** — 綴り間違いで「起こしたつもりのワーカーが
   動いていない」状態になる。
2. **既定ではワーカーを起こさない** — 設定が済んでいないワーカーを起こすと、警告だけ出して
   何もしないプロセスが並ぶ。
"""

from __future__ import annotations

import pytest
from scripts.dev import APP_FACTORY, RELOAD_DIRS, WORKERS, parse_args, selected_workers


def test_the_default_starts_no_worker() -> None:
    """既定ではワーカーを起こさない。"""
    args = parse_args([])

    assert selected_workers(args.workers) == []


def test_workers_can_be_selected() -> None:
    """カンマ区切りで選べる。"""
    assert selected_workers("belief,reconcile") == ["belief", "reconcile"]
    assert selected_workers(" belief , bridge ") == ["belief", "bridge"]


def test_an_unknown_worker_name_exits() -> None:
    """未知の名前は終了コード 2 で落ちる（黙って飛ばさない）。"""
    with pytest.raises(SystemExit) as caught:
        selected_workers("belif")

    assert caught.value.code == 2


def test_all_four_workers_are_selectable() -> None:
    """CLAUDE.md が挙げる 4 ワーカーがすべて選べる。"""
    assert set(WORKERS) == {"bridge", "belief", "reconcile", "scheduler"}


def test_defaults_are_local_only() -> None:
    """既定の待受はローカルだけ（開発用の認証が外に出ない）。"""
    args = parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.no_reload is False


def test_reload_does_not_watch_tests() -> None:
    """ホットリロードは ``tests`` を監視しない（テスト編集で API が再起動しない）。"""
    assert "tests" not in RELOAD_DIRS
    assert set(RELOAD_DIRS) == {"src", "services"}


def test_the_factory_is_an_import_string() -> None:
    """uvicorn に渡すのは import 文字列（ホットリロードが要求する）。"""
    assert APP_FACTORY == "sbp.api.factory:build_app"
