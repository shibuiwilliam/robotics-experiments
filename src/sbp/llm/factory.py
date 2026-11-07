"""設定からプロバイダを組む（M8-10、ADR-0022）。

- ``replay``: fixture のみ。SDK も鍵も要らない（既定）。
- ``record`` / ``live``: anthropic の live を ``ReplayProvider`` に包む。**鍵・モデルが無ければ
  失敗**し、
  黙って replay や別のプロバイダに落ちない。``SBP_LLM_DEFAULT`` が anthropic 以外なら失敗する
  （他社の live は未実装）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from sbp.core.errors import LlmError
from sbp.llm.replay import LlmMode, ReplayProvider, resolve_provider

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from sbp.core.config import Settings

__all__ = ["build_provider"]


def build_provider(
    settings: Settings, *, root: Path | None = None, client: httpx.Client | None = None
) -> ReplayProvider:
    """設定からプロバイダを組む。

    Args:
        settings: 設定（``llm_mode``・``llm_default``・鍵とモデル）。
        root: fixture の根（既定はリポジトリ相対）。
        client: HTTP クライアント（テストで ``MockTransport`` を渡す）。

    Raises:
        LlmError: ``record`` / ``live`` で、プロバイダが anthropic でない、鍵・モデルが無い場合。
    """
    mode = LlmMode(settings.llm_mode.value)
    if mode is LlmMode.REPLAY:
        return resolve_provider(mode=mode, root=root)
    if settings.llm_default != "anthropic":
        raise LlmError(
            f"SBP_LLM_MODE={mode.value} with SBP_LLM_DEFAULT={settings.llm_default}: only "
            "anthropic has a live implementation (ADR-0022)"
        )
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value():
        raise LlmError(
            f"SBP_LLM_MODE={mode.value} needs ANTHROPIC_API_KEY in .env; refusing to fall back "
            "to another path silently"
        )

    def live() -> object:
        from sbp.llm.anthropic import AnthropicProvider  # noqa: PLC0415 — replay では読まない

        return AnthropicProvider(
            api_key=SecretStr(key.get_secret_value()),
            model=settings.anthropic_model,
            client=client,
        )

    return resolve_provider(mode=mode, live_factory=live, root=root)
