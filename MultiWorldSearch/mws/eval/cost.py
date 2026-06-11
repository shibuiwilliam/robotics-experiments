"""Cloud call and cost counter."""

from __future__ import annotations

from dataclasses import dataclass

from mws.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CloudCostTracker:
    """Tracks cloud API calls, tokens, and estimated costs.

    LLM calls are split into REAL (an actual cloud round-trip happened, e.g.
    the live ADK agent) and MODELED (a cost-model entry for a mock agent step —
    no network traffic). `llm_calls` remains the sum for backward
    compatibility, but reports must use the split keys; conflating the two
    overstated cloud usage in earlier reports (IMPROVEMENT M8).
    """

    #: One API REQUEST = one count (E2: a batched request embedding N texts
    #: counts 1 request / N texts). ``embedding_calls`` stays as a compat
    #: alias for requests — the reconciliation audit counts requests.
    embedding_requests: int = 0
    embedding_texts: int = 0
    embedding_tokens: int = 0
    llm_calls_real: int = 0
    llm_calls_modeled: int = 0
    #: Real calls whose token counts came from the API's usage_metadata (M13)
    #: rather than the chars/4 estimate.
    llm_calls_tokens_measured: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    _cost_per_1k_embedding_tokens: float = 0.00004  # estimated
    _cost_per_1k_llm_input_tokens: float = 0.00015
    _cost_per_1k_llm_output_tokens: float = 0.0006

    def record_embedding_call(self, tokens: int = 0, *, texts: int = 1) -> None:
        """Record one embedding API REQUEST covering ``texts`` input texts."""
        self.embedding_requests += 1
        self.embedding_texts += texts
        self.embedding_tokens += tokens

    @property
    def embedding_calls(self) -> int:
        """Compat alias: number of API requests (the reconciled quantity)."""
        return self.embedding_requests

    def record_llm_call(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        *,
        real: bool = False,
        measured: bool = False,
    ) -> None:
        """Record one LLM call.

        ``real=True`` ONLY for an actual cloud call; ``measured=True`` ONLY
        when the token counts came from the API's usage_metadata (M13).
        """
        if real:
            self.llm_calls_real += 1
            if measured:
                self.llm_calls_tokens_measured += 1
        else:
            self.llm_calls_modeled += 1
        self.llm_input_tokens += input_tokens
        self.llm_output_tokens += output_tokens

    @property
    def llm_calls(self) -> int:
        """Total LLM-call entries (real + modeled). Prefer the split keys."""
        return self.llm_calls_real + self.llm_calls_modeled

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self.embedding_tokens / 1000 * self._cost_per_1k_embedding_tokens
            + self.llm_input_tokens / 1000 * self._cost_per_1k_llm_input_tokens
            + self.llm_output_tokens / 1000 * self._cost_per_1k_llm_output_tokens
        )

    def summary(self) -> dict[str, float | int]:
        return {
            "embedding_calls": self.embedding_calls,
            "embedding_requests": self.embedding_requests,
            "embedding_texts": self.embedding_texts,
            "embedding_tokens": self.embedding_tokens,
            "llm_calls": self.llm_calls,
            "llm_calls_real": self.llm_calls_real,
            "llm_calls_modeled": self.llm_calls_modeled,
            "llm_calls_tokens_measured": self.llm_calls_tokens_measured,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }
