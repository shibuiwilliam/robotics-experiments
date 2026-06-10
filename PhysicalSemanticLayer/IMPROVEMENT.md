# IMPROVEMENT.md — Real LLM Baseline (B2-Live) Integration Plan

> **Date**: 2026-06-10
> **Goal**: Replace B2's statistical simulation with actual Claude API calls for schema translation, enabling honest PSL vs LLM comparison

---

## 1. What Exists Today

`eval/baselines.py` の `BaselineB2` は LLM の翻訳を **シミュレート** しています:

```python
class BaselineB2:
    def translate(self, source_state):
        result = invert_schema_transform(source_state, self._transform)  # 正解を知っている
        jpos += self._rng.normal(0, 1e-4, size=jpos.shape)              # 数値ノイズ追加
        if self._rng.random() < 0.01:                                    # 1%の確率で単位忘れ
            jpos = source_state["joint_positions"]
        return result
```

これは LLM の **構造的弱点** （数値精度、確率的ミス）をモデル化していますが、実際の LLM が同じタスクでどう振る舞うかは測定していません。

---

## 2. What We Need

実際の Claude API を呼んでスキーマ変換させる `BaselineB2Live` を追加し、以下を測定する:

| 指標 | 目的 |
|------|------|
| **RMSE** | LLM の翻訳精度（シミュレーションと比較） |
| **非決定性** | 同一入力に対する出力のばらつき（温度0でも発生するか） |
| **レイテンシ** | 1回の翻訳にかかる時間（PSL の 125μs と比較） |
| **コスト** | 1回あたりの API コスト |
| **失敗モード** | パース失敗、単位混同、桁落ちなどの実際のエラーパターン |

---

## 3. 設計

### 3a. プロンプト設計

LLM に渡すプロンプトは、スキーマ変換の仕様を明示した上で状態データを渡す形にする:

```
You are a robot state translator.

Source robot: joint positions in milliradians (mrad), 7 joints.
Target robot: joint positions in radians (rad), 7 joints.

Convert by dividing each value by 1000.

Input joint_positions (mrad):
[-12.091, -3.090, 0.013, -63.904, -0.016, 0.832, -0.000]

Return ONLY a JSON array of 7 numbers in radians. No explanation.
```

プロンプトには **変換方法を明示する** （"divide by 1000"）。これは LLM に最も有利な条件であり、「変換方法を知っている LLM」と「PSL の Canonical IR」の公平な比較になる。変換方法を隠す（LLM に推測させる）テストは別途検討する。

### 3b. BaselineB2Live クラス

`eval/baselines.py` に追加:

```python
class BaselineB2Live:
    """B2-Live: Actual Claude API calls for schema translation.
    
    Calls Claude (Haiku for cost efficiency) to translate robot state
    between schemas. Measures real LLM translation accuracy, latency,
    and failure modes.
    """
    
    def __init__(self, transform: SchemaTransform, model: str = "claude-haiku-4-5"):
        self._transform = transform
        self._model = model
        self._call_count = 0
        self._total_cost = 0.0
        self._latencies: list[float] = []
        self._parse_failures = 0
    
    async def translate(self, source_state: dict) -> dict:
        """Call Claude API to translate the state."""
        prompt = self._build_prompt(source_state)
        # Call Claude via SDK query()
        # Parse response as JSON array
        # Return translated state
    
    def _build_prompt(self, source_state: dict) -> str:
        """Build translation prompt with schema spec + state data."""
```

### 3c. API 呼び出しの実装

Claude Agent SDK の `query()` を使用:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(prompt=prompt, options=ClaudeAgentOptions(model=model)):
    if isinstance(message, ResultMessage):
        response_text = message.result
        break
```

レスポンスを JSON パースし、パース失敗時は `parse_failures` をカウントして元の値を返す（フォールバック）。

### 3d. 測定の構成

```python
def run_baseline_comparison(base_state, transform, rng, seed=42, include_live_llm=False):
    results = {}
    # ... existing B0, B1, B2 (simulation), PSL ...
    
    if include_live_llm:
        b2_live = BaselineB2Live(transform)
        t0 = time.perf_counter()
        b2_live_result = await b2_live.translate(hetero_state)
        latency = time.perf_counter() - t0
        results["B2-Live"] = {
            "joint_rmse": joint_rmse(...),
            "info_loss": round_trip_information_loss(...),
            "latency_ms": latency * 1000,
            "api_calls": 1,
            "cost_usd": b2_live._total_cost,
            "parse_failures": b2_live._parse_failures,
        }
    
    return results
```

### 3e. 非同期対応

`query()` は async なので、`run_baseline_comparison` も async 版を用意するか、`asyncio.run()` でラップする。既存のオフラインモードに影響を与えないよう、`include_live_llm=False` をデフォルトにする。

---

## 4. 実装計画

### Phase 1: BaselineB2Live の実装

1. `eval/baselines.py` に `BaselineB2Live` クラスを追加
2. プロンプト生成: transform のパラメータ（unit_scale, frame_rotation 等）から変換仕様を自然言語化
3. Claude API 呼び出し: `query()` で Haiku を使用（コスト最小化）
4. レスポンスパース: JSON 配列をパースし、失敗時はフォールバック + エラーカウント
5. メトリクス記録: RMSE, latency, cost, parse_failures

### Phase 2: 呼び出し統合

1. `run_baseline_comparison()` に `include_live_llm: bool = False` パラメータを追加
2. `True` の場合のみ B2-Live を実行（既存テストには影響なし）
3. 結果を `baseline_results["B2-Live"]` として返す

### Phase 3: Makefile + 実行

1. `make baseline-llm` ターゲットを追加（B2-Live 実行、$$マーク付き）
2. 1回の翻訳テスト（S1 の transform で1回呼び出し）で動作確認
3. 6段階 dose-response sweep で B2-Live の劣化曲線を測定
4. 結果を `experiments/runs/b2_live_results.json` に保存

### Phase 4: 比較分析

1. B2（シミュレーション）vs B2-Live（実LLM）の RMSE 比較
2. 非決定性テスト: 同一入力を5回翻訳し、出力のばらつきを測定
3. 失敗モード分析: パース失敗率、単位混同率、桁落ちのパターン
4. レイテンシ比較: PSL (125μs) vs B2-Live (推定 500ms-2s)

### Phase 5: レポートとブログ更新

1. REPORT.md に B2-Live の結果セクションを追加
2. blog.ja.md の「実 LLM との比較は今後の課題」を実測データに置き換え
3. B2（シミュレーション）の設計が実 LLM の振る舞いとどの程度一致したかを報告

---

## 5. コスト見積もり

| 項目 | 呼び出し数 | コスト |
|------|----------|--------|
| 動作確認 | 1回 | ~$0.001 |
| Dose-response (6段階) | 6回 | ~$0.006 |
| 非決定性テスト (5回) | 5回 | ~$0.005 |
| 7シナリオ各1回 | 7回 | ~$0.007 |
| **合計** | **~19回** | **~$0.02** |

Haiku を使えばコストは無視できるレベルです。

---

## 6. 設計判断

### なぜ Haiku を使うか

- **コスト効率**: Haiku は Sonnet/Opus の 1/10〜1/50 のコスト
- **公平性**: 翻訳タスクは「配列を 1000 で割る」程度の単純作業。最高性能モデルを使う必要はない
- **ただし**: Haiku でも PSL より遅い（500ms vs 125μs）ことを示せれば、モデルサイズによらない構造的優位を主張できる
- **オプション**: Sonnet/Opus でも測定し、モデル間の比較も提示する

### なぜプロンプトに変換方法を明示するか

- LLM に **最も有利な条件** を与える（変換方法を推測させない）
- これでも PSL に劣る部分（レイテンシ、非決定性、共分散非伝播）が構造的弱点であることを示す
- 「プロンプトが悪かっただけ」という反論を封じる

### 既存テストへの影響

- `include_live_llm=False` がデフォルトなので、既存の 277 テストと 7 シナリオは一切影響を受けない
- B2-Live は `@pytest.mark.api` テストとして隔離（`make test-fast` からは除外）

---

## 7. 成功基準

1. B2-Live の RMSE が測定でき、B2（シミュレーション）と比較可能
2. レイテンシが PSL の 125μs と比較可能な形で記録される
3. 非決定性（同一入力での出力分散）が定量化される
4. blog.ja.md から「実 LLM との比較は今後の課題」が消え、実測データに置き換わる
5. 既存テスト（277テスト）に影響なし

---

## 8. Previously Resolved Items

| ID | Issue | Resolution |
|----|-------|------------|
| I1 | S6 synthetic coin-flip | Fixed: real CLIP predict_affordances() |
| I2 | B2 perfect oracle | Fixed: realistic noise + 1% unit confusion |
| I3 | Clock skew self-fulfilling | Fixed: 5ms NTP uncertainty |
| I4 | RMSE=0.0 framed as finding | Fixed: labeled as sanity check |
| V1 | SmolVLA integration | Fixed: transformers==5.3.0, all 7 scenarios active |
| B2L | B2 simulated, never measured against a real LLM | **Done**: `BaselineB2Live` calls Claude (`claude-haiku-4-5`) via the Agent SDK. Measured RMSE/latency/cost/non-determinism/failure-modes (`make baseline-llm`, [REPORT.md](REPORT.md)). Real LLM is *more* accurate than the sim but ~2000× slower, ~$0.004–0.03/call, no covariance/provenance. Offline suite unaffected (`include_live_llm=False` default; `@pytest.mark.api`). |

---

## 9. Implementation Status (2026-06-10)

All 5 phases of §4 complete. Real measured data: see [`REPORT.md`](REPORT.md) and
`experiments/runs/b2_live_results.json`.

| Success criterion (§7) | Status |
|---|---|
| 1. B2-Live RMSE measured, comparable to B2-sim | ✅ dose-response table in REPORT §2.1 |
| 2. Latency recorded vs PSL 125 µs | ✅ ~2374 ms vs ~1.2 ms (~2000×) |
| 3. Non-determinism quantified | ✅ identical across 5 runs (max_spread=0) for this task |
| 4. blog.ja.md "future work" → measured data | ✅ all three mentions replaced |
| 5. Existing tests unaffected | ✅ `make test-fast` green (198 passed); +14 unit / +1 api added |

---

## 10. Findings from the online validation run (`make scenarios-all`, 2026-06-10)

All 7 scenarios passed online (real Claude agents; see [`REPORT.md`](REPORT.md) §2). These findings do **not**
overturn the pass/fail results — they are gaps in the *logging/instrumentation* that limit what can be concluded
and, in one case, leak cost. Listed by priority. IDs continue from §8.

| ID | Severity | Issue | Evidence | Proposed fix |
|----|----------|-------|----------|--------------|
| **D1** | **high** | `make test` bills the real Anthropic API. Scenario oracle tests are not marked `@pytest.mark.api`, and `detect_mode()` auto-selects **online** whenever `ANTHROPIC_API_KEY` is set. So `make test` (which only excludes `-m api`) makes real agent calls. | During this session `make test` billed **~$0.98 across 11 scenario runs**, concurrently with `scenarios-all`. | Force offline in the test suite: set `PSL_MODE=offline` in `tests/conftest.py` (or a pytest fixture), and/or mark the online-capable scenario tests `@pytest.mark.api`. Add a guard so default `make test` can never call the API. |
| **D2** | medium | `smolvla_action_confidence` is a hardcoded constant `0.8`. | `src/psl/grounding/smolvla_encoder.py:190` returns `confidence=0.8` for every success; all 7 manifests show 0.8. `ee_delta` *is* real (varies per object in S6). | Derive a genuine confidence from the model (e.g. action-head logit/entropy or ensemble variance), or rename the field to a constant and stop treating it as a measured signal. Any safety margin keyed on it is currently non-informative. |
| **D3** | medium | `physical_accuracy_ee` / `physical_accuracy_object` are identically **0.0** in all 7 scenarios — no real physical task-accuracy signal. | `eval/scenarios/orchestrator.py:258/268` compares `ee_position` against the EE ground-truth *site*, but both derive from the same reading → trivially zero. | Compare the **commanded/achieved** grasp pose against the object's ground-truth body pose after a real grasp closure, or remove the metric until a closed-loop grasp exists (cross-ref the open SmolVLA closed-loop gap in `blog.ja.md`). |
| **D4** | medium | S7 `fidelity_by_skew` is flat **0.0** at every skew (0.0…1.0 s); the "degradation curve" has no signal and the monotonicity check passes trivially. | `eval/scenarios/s7_degraded_ops/eval.py:61-80`; manifest `fidelity_by_skew={'0.0':0.0,...,'1.0':0.0}`. | Verify with an oracle whether PSL is genuinely skew-invariant on this path; if not, make the metric sensitive to the injected skew (it should degrade as skew exceeds declared clock uncertainty). |
| **D5** | low | Agent turn/tool-call counts captured only for **S1**. s2–s7 record `agent_cost_usd` but not `agent_num_turns`/`agent_tool_calls`. | Only `eval/scenarios/s1_mixed_fleet_pick/eval.py` writes those keys. | Hoist the `agent_num_turns`/`agent_tool_calls` recording into the orchestrator so every scenario captures it (needed for the RQ5 extension-cost story). |
| **D6** | low | No consolidated online-run summary artifact. `scenarios-all` emits only stdout one-liners + 7 separate manifests; post-hoc analysis is fragile (and was polluted by D1's concurrent runs). | Had to mine `experiments/runs/*/manifest.json` and disambiguate against concurrent runs. | Emit a single `experiments/runs/<ts>-scenarios_all/summary.json` (scenario → {pass, key metrics, cost}) from `_run-scenarios`. |
| **D7** | low | Manifest `git_commit` records HEAD (`939d1c9`), not the uncommitted `feat/b2-live` working tree the run actually used. | All §2 manifests. | Commit the branch before authoritative runs, or have the manifest flag a dirty working tree (CLAUDE.md §10). |
