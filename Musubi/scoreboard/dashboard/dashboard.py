"""Semantic-observability dashboard (``make dashboard``).

Renders a self-contained HTML page: the ablation-ladder summary for a scenario, plus one Case's
end-to-end trace (order→motor) reconstructed from the bus — the NFR-OBS view where a single Case
IRI threads every instance's actions.
"""

from __future__ import annotations

import html
from pathlib import Path

from bench.runner.arm import Arm
from bench.runner.assemble import build_from_scenario
from bench.runner.run import run_scenario
from bench.scenarios.loader import load_scenario
from scoreboard.metrics import MetricsStore

_DASH_DIR = Path(__file__).resolve().parent

_CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1a1a2e;background:#fafafc}
h1{color:#16213e} h2{color:#0f3460;margin-top:2rem;border-bottom:2px solid #e0e0ef;padding-bottom:.3rem}
table{border-collapse:collapse;margin:1rem 0;width:100%} th,td{border:1px solid #d8d8e8;padding:.4rem .7rem;text-align:right}
th{background:#0f3460;color:#fff} td:first-child,th:first-child{text-align:left}
.ok{color:#0a7d3f;font-weight:600} .bad{color:#c62828;font-weight:600}
.trace{list-style:none;padding:0} .trace li{padding:.35rem .7rem;margin:.2rem 0;background:#fff;border-left:3px solid #0f3460;border-radius:3px}
.t{color:#888;font-variant-numeric:tabular-nums;margin-right:.6rem} .badge{display:inline-block;background:#0f3460;color:#fff;border-radius:3px;padding:0 .4rem;margin-right:.5rem;font-size:12px}
small{color:#666}
"""


def _summary_table(scenario_name: str) -> str:
    records = run_scenario(scenario_name)
    store = MetricsStore(":memory:")
    store.ingest([r.to_dict() for r in records])
    rows = store.arm_summaries(scenario_name)
    store.close()
    body = "".join(
        f"<tr><td>{s.arm}</td><td>{s.n}</td><td class='{_c(s.oracle_pass_rate == 1)}'>"
        f"{s.oracle_pass_rate:.2f}</td><td>{s.success_rate:.2f}</td>"
        f"<td class='{_c(s.unapproved_irreversible == 0)}'>{s.unapproved_irreversible}</td>"
        f"<td>{s.mean_trace_completeness:.2f}</td><td>{s.total_api_calls}</td></tr>"
        for s in rows
    )
    return (
        "<table><tr><th>arm</th><th>n</th><th>oracle</th><th>success</th>"
        "<th>unappr-irrev</th><th>mean-trace</th><th>API</th></tr>" + body + "</table>"
    )


def _trace_view(scenario_name: str) -> str:
    """Reconstruct one A4 Case trace from the bus (order→motor)."""
    scenario = load_scenario(scenario_name)
    if "A4" not in scenario.arms:
        return "<p><small>no A4 arm to trace</small></p>"
    episode, goal, _world, _pert = build_from_scenario(scenario, "A4", scenario.seeds[0])
    episode.run(goal)
    items = []
    for env in episode.bus.delivered():
        payload = html.escape(str(env.payload))
        items.append(
            f"<li><span class='t'>t={env.sim_time:.2f}s</span>"
            f"<span class='badge'>{html.escape(env.event_type)}</span>"
            f"<code>{html.escape(env.id)}</code> — {payload}"
            f"<br><small>trace={html.escape(str(env.trace))} · source={html.escape(str(env.source))}</small></li>"
        )
    return f"<ul class='trace'>{''.join(items)}</ul>"


def _c(ok: bool) -> str:
    return "ok" if ok else "bad"


def build_dashboard(scenario_name: str = "e0_smoke", out_path: Path | None = None) -> Path:
    Arm.from_name("A4")  # validate the ladder exists
    summary = _summary_table(scenario_name)
    trace = _trace_view(scenario_name)
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Musubi — Semantic Observability</title><style>{_CSS}</style></head><body>
<h1>Musubi — Semantic Observability</h1>
<p><small>Scenario <b>{html.escape(scenario_name)}</b>. Read-only view; scores use god-view oracles.</small></p>
<h2>Ablation ladder (A0→A4)</h2>
{summary}
<h2>Case trace (order → motor, A4)</h2>
<p><small>One Case IRI threads every instance's actions on the bus (NFR-OBS).</small></p>
{trace}
</body></html>
"""
    out = out_path or (_DASH_DIR / "index.html")
    out.write_text(page, encoding="utf-8")
    return out
