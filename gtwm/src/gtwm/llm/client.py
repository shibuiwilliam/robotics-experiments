"""LLM クライアント（.claude/rules/llm.md）。

`gtwm.llm.client.LLMClient` だけが `anthropic`/`openai`/`google-genai` SDK を import
してよい唯一のモジュール（llm.md「呼出は gtwm.llm.client.LLMClient のみ」）。他の
モジュール（grounding/wm/sim）からこれらの SDK を直接 import してはならない。

モデル名は `configs/llm.yaml` の `tasks.<task>.provider/model` からのみ決まる。
失敗時に他プロバイダへ自動フォールバックはしない（エラーにする）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from gtwm.utils.config import load_config, to_container
from gtwm.utils.paths import repo_root

Provider = Literal["anthropic", "openai", "gemini", "mock"]

_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}


class LLMNotConfiguredError(RuntimeError):
    """プロバイダの API キーが未設定、またはタスク設定が存在しない場合。"""


class LLMBudgetExceededError(RuntimeError):
    """`LLM_MONTHLY_BUDGET_USD` を超過した場合。フォールバックはしない（エラーのみ）。"""


class LLMCallError(RuntimeError):
    """プロバイダ呼出自体が失敗した場合（自動フォールバックはしない）。"""


@dataclass
class PingResult:
    provider: str
    ok: bool
    detail: str


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cached: bool
    duration_s: float


def _repo_path(*parts: str) -> Path:
    return repo_root().joinpath(*parts)


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _schema_hash(schema: type[BaseModel] | None) -> str:
    if schema is None:
        return "none"
    schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
    return hashlib.sha256(schema_json.encode()).hexdigest()


class LLMCache:
    """`.data/llm_cache.sqlite`。キーは (provider, model, prompt hash, schema hash)。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _repo_path(".data", "llm_cache.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS cache (
                provider TEXT, model TEXT, prompt_hash TEXT, schema_hash TEXT,
                response_text TEXT, input_tokens INTEGER, output_tokens INTEGER,
                PRIMARY KEY (provider, model, prompt_hash, schema_hash)
            )"""
        )
        self._conn.commit()

    def get(
        self, provider: str, model: str, prompt_hash: str, schema_hash: str
    ) -> tuple[str, int, int] | None:
        row = self._conn.execute(
            "SELECT response_text, input_tokens, output_tokens FROM cache "
            "WHERE provider=? AND model=? AND prompt_hash=? AND schema_hash=?",
            (provider, model, prompt_hash, schema_hash),
        ).fetchone()
        return tuple(row) if row else None

    def put(
        self,
        provider: str,
        model: str,
        prompt_hash: str,
        schema_hash: str,
        response_text: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?)",
            (provider, model, prompt_hash, schema_hash, response_text, input_tokens, output_tokens),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _estimate_cost(
    pricing: dict[str, Any], model: str, input_tokens: int, output_tokens: int
) -> float | None:
    entry = pricing.get(model)
    if not entry or entry.get("input_per_1m") is None or entry.get("output_per_1m") is None:
        return None
    return (input_tokens / 1_000_000) * entry["input_per_1m"] + (output_tokens / 1_000_000) * entry[
        "output_per_1m"
    ]


def _read_monthly_usage_usd(usage_path: Path, year_month: str) -> float:
    if not usage_path.exists():
        return 0.0
    total = 0.0
    with usage_path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("timestamp", "").startswith(year_month) and rec.get("cost_usd") is not None:
                total += float(rec["cost_usd"])
    return total


class LLMClient:
    """`configs/llm.yaml` のタスク割当に基づき、指定プロバイダへ問い合わせる。"""

    def __init__(
        self,
        config_path: str = "configs/llm.yaml",
        cache: LLMCache | None = None,
        usage_log_path: Path | None = None,
    ) -> None:
        self._config = to_container(load_config(config_path))
        self._cache = cache or LLMCache()
        self._usage_log_path = usage_log_path or _repo_path("runs", "llm_usage.jsonl")
        self._usage_log_path.parent.mkdir(parents=True, exist_ok=True)

    def _task_provider_model(self, task: str) -> tuple[str, str]:
        tasks = self._config.get("tasks", {})
        if task not in tasks:
            raise LLMNotConfiguredError(f"configs/llm.yaml に task={task} の設定がありません")
        entry = tasks[task]
        return entry["provider"], entry["model"]

    def _check_budget(self) -> None:
        budget_str = os.environ.get("LLM_MONTHLY_BUDGET_USD")
        if not budget_str:
            return
        budget = float(budget_str)
        year_month = time.strftime("%Y-%m")
        spent = _read_monthly_usage_usd(self._usage_log_path, year_month)
        if spent >= budget:
            raise LLMBudgetExceededError(
                f"LLM_MONTHLY_BUDGET_USD={budget} を超過（今月の記録上の使用額={spent:.4f}）"
            )

    def ping(self, provider: Provider) -> PingResult:
        """プロバイダへの疎通を確認する。キー未設定は「未設定」として ok=False で返す
        （クラッシュさせない）。"""
        if provider == "mock":
            return PingResult(provider, True, "mock プロバイダ（常に利用可能）")
        env_name = _API_KEY_ENV[provider]
        api_key = os.environ.get(env_name)
        if not api_key:
            return PingResult(provider, False, f"未設定（環境変数 {env_name} が空）")
        try:
            if provider == "anthropic":
                import anthropic

                client = anthropic.Anthropic(api_key=api_key)
                client.models.list(limit=1)
            elif provider == "openai":
                import openai

                oclient = openai.OpenAI(api_key=api_key)
                oclient.models.list()
            elif provider == "gemini":
                from google import genai

                gclient = genai.Client(api_key=api_key)
                list(gclient.models.list())
        except Exception as exc:  # noqa: BLE001 - 疎通確認なので例外種別を問わず報告する
            return PingResult(provider, False, f"疎通失敗: {exc}")
        return PingResult(provider, True, "OK")

    def call(
        self,
        task: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """`task` に紐づくプロバイダ/モデルで呼び出す。JSON期待タスク（schema指定時）は
        温度0・max_tokens明示・最大2回まで再試行する。"""
        provider, model = self._task_provider_model(task)
        self._check_budget()

        prompt_hash = _prompt_hash(prompt)
        schema_hash = _schema_hash(schema)
        cached = self._cache.get(provider, model, prompt_hash, schema_hash)
        start = time.time()
        if cached is not None:
            text, in_tok, out_tok = cached
            response = LLMResponse(
                text=text,
                provider=provider,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=0.0,
                cached=True,
                duration_s=time.time() - start,
            )
            self._log_usage(task, response)
            return response

        max_retries = int(self._config.get("retry", {}).get("max_retries", 2))
        temperature = float(self._config.get("retry", {}).get("temperature_for_json_tasks", 0.0))

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                text, in_tok, out_tok = self._dispatch(
                    provider, model, prompt, max_tokens, temperature if schema else None
                )
                if schema is not None:
                    schema.model_validate_json(text)
                break
            except Exception as exc:  # noqa: BLE001 - リトライ対象を広く受ける
                last_exc = exc
                if attempt == max_retries:
                    raise LLMCallError(
                        f"provider={provider} model={model} task={task} 呼出失敗: {exc}"
                    ) from exc
        else:  # pragma: no cover - for 文が break せず終わることは無い
            raise LLMCallError(f"呼出失敗: {last_exc}")

        self._cache.put(provider, model, prompt_hash, schema_hash, text, in_tok, out_tok)
        pricing = self._config.get("pricing", {})
        cost = _estimate_cost(pricing, model, in_tok, out_tok)
        response = LLMResponse(
            text=text,
            provider=provider,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            cached=False,
            duration_s=time.time() - start,
        )
        self._log_usage(task, response)
        return response

    def _dispatch(
        self,
        provider: str,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float | None,
    ) -> tuple[str, int, int]:
        if provider == "mock":
            return self._dispatch_mock(prompt)
        api_key = os.environ.get(_API_KEY_ENV[provider])
        if not api_key:
            raise LLMNotConfiguredError(
                f"provider={provider} は未設定です（環境変数 {_API_KEY_ENV[provider]} が空）"
            )
        effective_temperature = 1.0 if temperature is None else temperature
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            # NOTE: 現行の anthropic SDK 型は messages.create() に temperature を
            # 露出していないため未指定で呼ぶ（JSON厳格出力タスクは openai に割り当てる
            # 設計、configs/llm.yaml 参照）。
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content if hasattr(b, "text"))
            return text, msg.usage.input_tokens, msg.usage.output_tokens
        if provider == "openai":
            import openai

            oclient = openai.OpenAI(api_key=api_key)
            oresp = oclient.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                temperature=effective_temperature,
            )
            text = oresp.choices[0].message.content or ""
            usage = oresp.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0
            return text, in_tok, out_tok
        if provider == "gemini":
            from google import genai

            gclient = genai.Client(api_key=api_key)
            gresp = gclient.models.generate_content(model=model, contents=prompt)
            text = gresp.text or ""
            usage_meta = getattr(gresp, "usage_metadata", None)
            in_tok = getattr(usage_meta, "prompt_token_count", 0) or 0
            out_tok = getattr(usage_meta, "candidates_token_count", 0) or 0
            return text, in_tok, out_tok
        raise LLMNotConfiguredError(f"未知の provider: {provider}")

    def _dispatch_mock(self, prompt: str) -> tuple[str, int, int]:
        """テスト用の決定的モック応答。schema検証を通すため、プロンプト末尾の
        JSON プレースホルダー変数を単純な決定的値で埋めた JSON を返す。"""
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        mock_json = json.dumps(
            {
                "proposed_label_ja": f"未知概念候補_{digest}",
                "proposed_label_en": f"unknown_concept_{digest}",
                "definition": "モック応答（実LLM未呼出）。",
                "relation_to_existing_classes": "不明（モック）。",
                "evidence_summary": "モック応答のためクラスタ特徴は反映されていません。",
            },
            ensure_ascii=False,
        )
        return mock_json, len(prompt.split()), len(mock_json.split())

    def _log_usage(self, task: str, response: LLMResponse) -> None:
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": task,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "duration_s": response.duration_s,
            "cached": response.cached,
        }
        with self._usage_log_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def monthly_usage_usd(self, year_month: str | None = None) -> float:
        year_month = year_month or time.strftime("%Y-%m")
        return _read_monthly_usage_usd(self._usage_log_path, year_month)

    def close(self) -> None:
        self._cache.close()
