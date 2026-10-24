"""設定（``sbp.core.config``）の単体テスト（X-11、``docs/PROGRESS.md`` 所見 108）。

重点:

1. **層別の閾値も ``.env`` から効く** — 入れ子の ``BaseSettings`` は ``default_factory`` で別々に
   構築され、親の ``env_file`` を引き継がない。持たせ忘れると ``SBP_BRIDGE_*`` などは環境変数
   からしか読まれず、``.env`` に書いた値が**警告無しに既定値になる**。
2. **環境変数は ``.env`` に勝つ** — 直下の項目と同じ優先順位（pydantic-settings の既定）。
3. **新しい層を足したときも同じ** — 入れ子の設定がすべて親と同じ ``.env`` を読むことを
   構造で検査する。
4. **``.env.example`` が全項目を載せている** — 載っていない項目は運用者が存在を知らず、
   ``.env`` で上書きできることに気付かない（X-13、``docs/PROGRESS.md`` 所見 112）。

単体テストはふだん ``Settings(...)`` を引数で組むので ``.env`` の読み込みを通らない。ここでは
一時ディレクトリに ``.env`` を置き、その中で ``Settings()`` を作る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings

from sbp.core.config import Settings

#: 各層から 1 項目ずつ（既定値と違う値にして、読まれたかどうかを見分ける）。
_NESTED_IN_DOTENV = {
    "SBP_BRIDGE_HYSTERESIS_HIGH": "0.8",
    "SBP_BRIDGE_PROBE_IDS": "https://sbp.example/probe/a/1.0.0,https://sbp.example/probe/b/1.0.0",
    "SBP_REASONING_THETA_NORM": "0.11",
    "SBP_WM_TORCH_DEVICE": "cpu",
    "SBP_ACTIONS_RECORD_EFFECT_FLOOR": "0.33",
}


@pytest.fixture
def in_dotenv_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``.env`` を置いた一時ディレクトリに移り、同名の環境変数を外す。

    環境変数が残っていると「環境変数が効いた」のか「``.env`` が効いた」のか区別できない
    （開発機は direnv で ``SBP_*`` を export していることがある。所見 60）。
    """
    for key in (*_NESTED_IN_DOTENV, "SBP_SITE_ID"):
        monkeypatch.delenv(key, raising=False)
    lines = [f"{key}={value}" for key, value in _NESTED_IN_DOTENV.items()]
    (tmp_path / ".env").write_text(
        "SBP_SITE_ID=siteZ\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("in_dotenv_dir")
class TestDotenvReachesEveryLayer:
    def test_top_level_items_are_read(self) -> None:
        """対照: 直下の項目は修正前から読めていた（読めないなら fixture が壊れている）。"""
        assert Settings().site_id == "siteZ"

    def test_bridge_thresholds_are_read(self) -> None:
        assert Settings().bridge.hysteresis_high == pytest.approx(0.8)

    def test_bridge_worker_wiring_is_read(self) -> None:
        """bridge-worker の必須設定（ADR-0009）。カンマ区切りが ``.env`` からも分割されること。"""
        assert Settings().bridge.probe_ids == (
            "https://sbp.example/probe/a/1.0.0",
            "https://sbp.example/probe/b/1.0.0",
        )

    def test_reasoning_thresholds_are_read(self) -> None:
        assert Settings().reasoning.theta_norm == pytest.approx(0.11)

    def test_worldmodel_settings_are_read(self) -> None:
        assert Settings().worldmodel.torch_device == "cpu"

    def test_actions_settings_are_read(self) -> None:
        assert Settings().actions.record_effect_floor == pytest.approx(0.33)

    def test_an_environment_variable_wins_over_dotenv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直下の項目と同じ優先順位。拠点の一時的な上書きを環境変数で入れられる。"""
        monkeypatch.setenv("SBP_REASONING_THETA_NORM", "0.2")
        assert Settings().reasoning.theta_norm == pytest.approx(0.2)


def _nested_settings() -> list[tuple[str, type[BaseSettings]]]:
    """``Settings`` の入れ子の設定（層別の閾値）をすべて返す。"""
    nested: list[tuple[str, type[BaseSettings]]] = []
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseSettings):
            nested.append((name, annotation))
    return nested


class TestEveryLayerSharesTheDotenv:
    def test_there_are_nested_layers(self) -> None:
        """陽性対照: 検査対象が 0 件なら下の検査は何も確かめていない。"""
        assert {name for name, _ in _nested_settings()} == {
            "bridge",
            "reasoning",
            "worldmodel",
            "actions",
        }

    @pytest.mark.parametrize(("name", "layer"), _nested_settings())
    def test_the_layer_reads_the_same_dotenv(self, name: str, layer: type[BaseSettings]) -> None:
        """層を足したときに ``env_file`` を持たせ忘れると落ちる。"""
        parent = Settings.model_config
        child = layer.model_config
        assert child.get("env_file") == parent.get("env_file"), name
        assert child.get("env_file_encoding") == parent.get("env_file_encoding"), name


ROOT = Path(__file__).resolve().parents[3]


def _setting_variables() -> set[str]:
    """``Settings`` の全項目の環境変数名（入れ子は層の接頭辞つき）。"""
    names: set[str] = set()
    nested = dict(_nested_settings())
    for name in Settings.model_fields:
        if name in nested:
            layer = nested[name]
            prefix = str(layer.model_config.get("env_prefix", ""))
            names.update(f"{prefix}{field}".upper() for field in layer.model_fields)
        else:
            names.add(f"SBP_{name}".upper())
    return names


class TestEnvExampleListsEverySetting:
    def test_every_setting_is_listed(self) -> None:
        """コメントアウトでもよい（既定値を見せる）。名前が無いことだけを落とす。"""
        listed = set(
            re.findall(
                r"^#?\s*(SBP_[A-Z0-9_]+)=",
                (ROOT / ".env.example").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        missing = sorted(_setting_variables() - listed)
        assert missing == [], f".env.example に無い設定: {missing}"

    def test_the_check_sees_nested_layers(self) -> None:
        """陽性対照: 入れ子の項目も数えている（直下だけを見ていたら上の検査は空振りする）。"""
        assert "SBP_REASONING_BP_PERIOD_S" in _setting_variables()
