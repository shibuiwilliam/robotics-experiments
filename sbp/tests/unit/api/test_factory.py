"""API の実配線の単体テスト（M8-13）。

**ここで確かめるのは「組み立てが落ちない」ことと「開発用の認証が本番に漏れない」ことである。**
実物のストアに繋ぐのは統合テストの仕事（接続は lifespan で開く）。

重点:

1. **`SBP_ENV=dev` 以外で開発用の要求者解決を作れない** — ヘッダを名乗るだけで権限が付くのは
   認証ではない（ADR-0008 の未決）。
2. **ヘッダが無ければ `None`** — `create_app` は未設定の要求者を全拒否する（既定は拒否）。
3. **`hypothetical` を許可しない** — 検討中の案が「決まった」と誤解される。
4. **配線できないものは渡さない** — 購読とコマンドは `None`（503 になる）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from sbp.api.factory import build_app, dev_principal_resolver
from sbp.core.config import Settings
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from collections.abc import Mapping

OPERATOR = "https://sbp.example/person/operator-1"


class _Request:
    """`Request` の代わり（ヘッダだけを見る）。"""

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers = headers


def _dev_settings(**overrides: Any) -> Settings:
    return Settings(env="dev", site_id="siteA", **overrides)


def test_a_header_names_the_principal() -> None:
    """ヘッダに主体 IRI を入れると要求者になる。"""
    resolve = dev_principal_resolver(_dev_settings())

    principal = resolve(_Request({"X-SBP-Principal": OPERATOR}))

    assert principal is not None
    assert principal.subject == OPERATOR


def test_without_the_header_there_is_no_principal() -> None:
    """ヘッダが無ければ ``None``（``create_app`` が全拒否する）。

    「未設定なら通す」経路を作らないことの確認である。
    """
    resolve = dev_principal_resolver(_dev_settings())

    assert resolve(_Request({})) is None
    assert resolve(_Request({"X-SBP-Principal": "   "})) is None


def test_the_dev_principal_cannot_see_hypothetical() -> None:
    """``hypothetical`` は許可しない。

    検討中の案が見えると「決まった」と誤解される（authorization の規約）。
    """
    resolve = dev_principal_resolver(_dev_settings())
    principal = resolve(_Request({"X-SBP-Principal": OPERATOR}))

    assert principal is not None
    modalities = principal.permissions[0].modalities
    assert Modality.HYPOTHETICAL not in modalities
    assert {Modality.OBSERVED, Modality.INFERRED, Modality.PREDICTED, Modality.PLANNED} == set(
        modalities
    )


def test_the_dev_principal_is_scoped_to_its_own_site() -> None:
    """自拠点だけを許可する（拠点間の情報分離）。"""
    resolve = dev_principal_resolver(_dev_settings())
    principal = resolve(_Request({"X-SBP-Principal": OPERATOR}))

    assert principal is not None
    assert principal.permissions[0].sites == frozenset({"siteA"})


@pytest.mark.parametrize("env", ["test", "prod"])
def test_the_dev_resolver_is_refused_outside_dev(env: str) -> None:
    """``dev`` 以外では開発用の要求者解決を作れない。

    **ここで落とすのが要点である**——本番でこの解決器が使われていたら、起動させてはいけない。
    """
    with pytest.raises(ValueError, match="not be used with SBP_ENV"):
        dev_principal_resolver(Settings(env=env))  # type: ignore[arg-type]


def test_build_app_does_no_io_at_construction() -> None:
    """組み立てだけでは接続しない（接続は lifespan で開く）。

    構築時に繋ぐと、インポートやテスト収集でネットワークが要る（単体テストは無ネットワーク）。
    """
    app = build_app(_dev_settings())

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/metrics" in paths
    assert "/health" in paths
    assert "/sparql" in paths


def test_build_app_outside_dev_has_no_principal_resolver() -> None:
    """``dev`` 以外では要求者解決を渡さない（= 信念クエリは全拒否）。

    本番の認証方式が決まるまで、API は信念を返さない側に倒す。
    """
    # 例外にならずに組み立てられる（解決器を渡さないだけ）
    app = build_app(Settings(env="prod", site_id="siteA"))

    assert app is not None
