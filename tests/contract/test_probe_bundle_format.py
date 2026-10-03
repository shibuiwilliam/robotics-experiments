"""プローブ束ね形式の契約テスト（M8-01）。

束ねは**プロセスの外に出る形式**である。置き場に入ったバイト列は、別の版のコードが読む。
したがってフィールドの追加・削除・改名は後方互換の判定を伴う必要があり、ここでは

1. 旧い（固定した）ペイロードが今のコードで読めること、
2. フィールド集合が ``schema_version`` を上げずに変わっていないこと

を検査する。golden を書き換えるときは ``BUNDLE_SCHEMA_VERSION`` の semver 判定を同じ
コミットで行う——**テストを通すために golden を直すのは、互換性を壊した記録を消すこと**である。
"""

from __future__ import annotations

import json

from sbp.bridge.probes import ProbeStatus
from sbp.bridge.serialization import (
    BUNDLE_SCHEMA_VERSION,
    ProbeBundle,
    dumps,
    loads,
    restore_probe,
)

GOLDEN: dict[str, object] = {
    "bias": -0.25,
    "calibration": {"kind": "temperature", "temperature": 1.5},
    "latent_dim": 3,
    "schema_version": "1.0.0",
    "spec": {
        "grounds": "https://sbp.example/ontology/core#Running",
        "kind": "classifier",
        "latent_level": "mid",
        "linear": True,
        "metrics": {
            "alignment_residual": 0.04,
            "auroc": 0.93,
            "ece": 0.03,
            "n_samples": 500,
        },
        "ontology_version": "0.5.0",
        "operating_modes": [],
        "probe_id": "https://sbp.example/probe/Running/1.0.0",
        "status": "active",
        "validity_sites": ["siteA"],
        "wm_version": "https://sbp.example/wm/koopman/0.1.0",
    },
    "trained": True,
    "weights": [0.5, -1.25, 0.0],
}
"""版 1.0.0 で固定したペイロード。**手で書いてある**（生成物から作ると、生成側が変わった
ときに golden も一緒に変わり、互換性の破れを検出できない）。"""

EXPECTED_FIELDS = {
    "schema_version",
    "spec",
    "latent_dim",
    "weights",
    "bias",
    "calibration",
    "trained",
}


def test_golden_payload_loads() -> None:
    """固定したペイロードが今のコードで読め、プローブとして復元できる。"""
    bundle = loads(json.dumps(GOLDEN).encode("utf-8"))

    assert bundle.schema_version == "1.0.0"
    assert bundle.spec.status is ProbeStatus.ACTIVE
    assert bundle.latent_dim == 3
    assert bundle.weights == (0.5, -1.25, 0.0)

    probe, spec = restore_probe(bundle)
    assert probe.dim == 3
    assert probe.bias == -0.25
    assert spec.validity_sites == ("siteA",)


def test_golden_payload_roundtrips_unchanged() -> None:
    """読んで書き戻すと同じ JSON になる（既定値の混入・落ちで形が変わらない）。"""
    restored = loads(json.dumps(GOLDEN).encode("utf-8"))
    assert json.loads(dumps(restored)) == GOLDEN


def test_field_set_is_fixed_for_this_schema_version() -> None:
    """フィールド集合が版を上げずに変わっていない。

    ``extra="forbid"`` があるので未知のフィールドは読み込み時に落ちるが、**こちら側が
    フィールドを増やした**ときは落ちない。増やすこと自体は MINOR だが、気付かずに増やすと
    旧版のコードが新しい束ねを読めるかどうかを誰も判定していない状態になる。
    """
    assert set(ProbeBundle.model_fields) == EXPECTED_FIELDS
    assert BUNDLE_SCHEMA_VERSION == "1.0.0", (
        "束ね形式の版が上がっている。GOLDEN と EXPECTED_FIELDS を見直し、"
        "ontology/CHANGELOG.md と同じ要領で semver 判定を記録すること"
    )
