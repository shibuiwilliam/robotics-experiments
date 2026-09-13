"""注入台帳（`data/injections/`）を読んでよい唯一のモジュール（盲検境界、
`.claude/rules/experiments.md`「盲検」節）。

`grounding/` と `wm/` はこのモジュール、または `data/injections` を直接参照しては
ならない（`tests/unit/test_blindness.py` が AST 解析で検査する）。乖離検知の
「型・対象が正しいか」の判定は、検知パイプライン自身ではなくこのモジュール
（採点側）だけが行う。

着手順8（P1相当、`.claude/rules/sim.md`）で `sim/wms_mock.py` の注入が実装される
まで `data/injections/<episode_id>.parquet` は存在しない。P0/smoke では注入無し
として扱う（台帳に何か出てきたら、それは定義上すべて誤報になる）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from gtwm.utils.paths import repo_root

INJECTIONS_DIR = "data/injections"
_INJECTION_COLUMNS = ["episode_id", "injection_type", "entity", "t_true", "detail"]


def load_injection_ledger(episode_id: str) -> pd.DataFrame:
    """`data/injections/<episode_id>.parquet` を読む。

    ファイルが存在しない場合（P0/smoke、または着手順8以前）は、列だけ揃えた空の
    DataFrame を返す（「注入0件」を意味する）。
    """
    path = repo_root() / INJECTIONS_DIR / f"{episode_id}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=_INJECTION_COLUMNS)
    return pd.read_parquet(path)


@dataclass
class DetectionScoring:
    """`score_detection()` の結果。付録A「乖離検知」の3指標の元になる生カウント。"""

    n_injected: int
    n_detected: int  # 対象(entity)・型(injection_type)が正しく一致した検知
    n_false_alarms: int  # 注入台帳に該当が無い台帳エントリ数
    matched_pairs: list[tuple[str, str]]  # (discrepancy_id, injection側の行の識別に使う文字列)
    latencies_s: list[float]  # 検知遅延（付録A）＝検知時刻－注入時刻、検知した分だけ
    detected_types: list[str]  # 検知できた注入の型（型別検知率の集計に使う）


def score_detection(
    ledger_entries: list[dict[str, object]],
    injection_ledger: pd.DataFrame,
    time_tolerance_s: float = 5.0,
) -> DetectionScoring:
    """乖離台帳のエントリと注入台帳を突き合わせる（付録A「乖離検知」）。

    検知の定義（poc_plan.md 3.2 H4）：「乖離台帳に該当エントリが生成され、対象物体と
    型が正しいこと」。`object_id` が一致し、`discrepancy_type` が注入の
    `injection_type` と一致し、検知時刻が注入時刻から `time_tolerance_s` 秒以内に
    ある場合に「検知」とする。それ以外の台帳エントリは全て誤報。

    `ledger_entries` は `grounding/ledger.py` の `DiscrepancyLedger.list_by_status()`
    等が返す dict のリストを想定（`object_id`, `discrepancy_type`, `detected_at` を
    持つこと）。
    """
    injected_rows = injection_ledger.to_dict("records")
    matched_injection_idx: set[int] = set()
    matched_pairs: list[tuple[str, str]] = []
    latencies_s: list[float] = []
    detected_types: list[str] = []
    n_detected = 0
    n_false_alarms = 0

    for entry in ledger_entries:
        found = False
        for i, inj in enumerate(injected_rows):
            if i in matched_injection_idx:
                continue
            if entry.get("object_id") != inj.get("entity"):
                continue
            if entry.get("discrepancy_type") != inj.get("injection_type"):
                continue
            detected_at = entry.get("detected_at")
            t_true = inj.get("t_true")
            dt = 0.0
            if detected_at is not None and t_true is not None:
                try:
                    # `ledger.py` は `entry.detected_at.isoformat()`（`grounding/probes.utc()`
                    # が `datetime.fromtimestamp(t_s, tz=UTC)` で作った、エピソード内時刻
                    # そのものを Unix epoch 起点として解釈した絶対時刻）で保存している。
                    # `.timestamp()` で元の episode-relative 秒に戻して比較する。
                    detected_s = datetime.fromisoformat(str(detected_at)).timestamp()
                    dt = abs(detected_s - float(str(t_true)))
                except (TypeError, ValueError):
                    dt = 0.0
                if dt > time_tolerance_s:
                    continue
            matched_injection_idx.add(i)
            matched_pairs.append((str(entry.get("discrepancy_id")), str(inj.get("entity"))))
            latencies_s.append(dt)
            detected_types.append(str(inj.get("injection_type")))
            n_detected += 1
            found = True
            break
        if not found:
            n_false_alarms += 1

    return DetectionScoring(
        n_injected=len(injected_rows),
        n_detected=n_detected,
        n_false_alarms=n_false_alarms,
        matched_pairs=matched_pairs,
        latencies_s=latencies_s,
        detected_types=detected_types,
    )


CONCEPT_INJECTION_TYPES = ("oversized_cargo_proxy", "reinspecting_step", "staging_overflow")
_LOG_HZ = 10  # sim/generate.py の LOG_HZ と一致させる（フレーム番号→秒への変換用）。


@dataclass
class ConceptDiscoveryScoring:
    """`score_concept_discovery()` の結果（H7、poc_plan.md 3.1「注入概念の候補上位5への
    出現率」）。"""

    injected_types: list[str]
    hit_types: list[str]
    n_candidates_considered: int


def score_concept_discovery(
    candidates: list[object],
    episode_ids: list[str],
    time_tolerance_s: float = 5.0,
) -> ConceptDiscoveryScoring:
    """概念発見の候補（上位5件、`grounding.concept_discovery.ConceptCandidateRecord`）が
    EXP-08 で注入した3種の未登録概念のうち何種に「命中」したかを数える。

    候補クラスタの各メンバーはスロットインデックスのみを持ち、フレーム間で永続する個体
    ID を持たない（同一性解決を経ていない生の残差）ため、個体単位の厳密な突き合わせは
    できない。代わりに、候補クラスタのメンバーが観測された時刻（episode_id, frame_idx）
    が、注入台帳に記録された概念注入イベントの時刻 `t_true` と `time_tolerance_s` 秒
    以内で重なるかどうかで「命中」を判定する（時間的近接性による代理指標）。
    """
    injection_events: list[tuple[str, str, float]] = []  # (episode_id, injection_type, t_true)
    for episode_id in episode_ids:
        ledger = load_injection_ledger(episode_id)
        for _, row in ledger.iterrows():
            inj_type = str(row["injection_type"])
            if inj_type in CONCEPT_INJECTION_TYPES:
                injection_events.append((episode_id, inj_type, float(row["t_true"])))

    hit_types: set[str] = set()
    for candidate in candidates:
        member_episode_frames = getattr(candidate, "member_episode_frames", [])
        for episode_id, frame_idx in member_episode_frames:
            t_candidate = frame_idx / _LOG_HZ
            for inj_episode_id, inj_type, t_true in injection_events:
                if inj_episode_id != episode_id:
                    continue
                if abs(t_candidate - t_true) <= time_tolerance_s:
                    hit_types.add(inj_type)

    return ConceptDiscoveryScoring(
        injected_types=sorted(set(CONCEPT_INJECTION_TYPES)),
        hit_types=sorted(hit_types),
        n_candidates_considered=len(candidates),
    )


__all__ = [
    "INJECTIONS_DIR",
    "load_injection_ledger",
    "DetectionScoring",
    "score_detection",
    "CONCEPT_INJECTION_TYPES",
    "ConceptDiscoveryScoring",
    "score_concept_discovery",
]
