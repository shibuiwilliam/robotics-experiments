"""EXP-10（H9、オペレータ評価）の準備：模擬例外10件、解決時間・正答率の計測、
SUS（System Usability Scale）の集計。

poc_plan.md 6.2「EXP-10 オペレータ評価」原文：
「現場リーダー・作業者5名以上に、模擬例外10件を従来手順とダッシュボードの両方で
処理してもらう（順序は無作為化）。出力：解決時間、正答率、訂正反映率、SUS、自由記述。」

**このモジュールは評価の「実施」を行わない。** 評価そのものは人（現場リーダー・
作業者5名以上）が行うものであり、Claude Code は模擬例外セットと記録機構（本
モジュールと `ui/app.py` の EXP-10 タブ）だけを用意する（CLAUDE.md「絶対条件」：
「EXP-10（人手評価）は人が手動で行う。Claude Code は模擬例外セットとUIだけを
用意する」）。本実行の手順は `docs/results/EXP-10_protocol.md` を参照。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gtwm.grounding.ledger import DISCREPANCY_TYPES, SEVERITIES

CONDITIONS = ("traditional", "dashboard")

# SUS標準10問（奇数問=肯定的表現、偶数問=否定的表現、poc_plan.md 6.2「SUS」）。
SUS_QUESTIONS = [
    "このシステムを頻繁に使いたいと思う。",
    "このシステムは不必要に複雑だと感じた。",
    "このシステムは使いやすいと思った。",
    "このシステムを使うには専門家の助けが必要だと思う。",
    "このシステムの様々な機能がうまく統合されていると感じた。",
    "このシステムには一貫性がなさすぎると感じた。",
    "ほとんどの人はこのシステムをすぐに使いこなせるようになると思う。",
    "このシステムは使うのが非常に煩わしいと感じた。",
    "このシステムを使うことに自信を持てた。",
    "このシステムを使い始める前に多くのことを学ぶ必要があった。",
]


@dataclass
class MockCase:
    case_id: str
    object_id: str
    discrepancy_type: str
    severity: str
    physical_evidence_note: str  # 「現物確認」で分かる情報（従来手順条件で提示）
    record_value: str  # 業務記録上の値（両条件で提示）
    record_source_system: str
    correct_resolution: str  # 採点用の正解（評価者には提示しない）


def generate_mock_cases() -> list[MockCase]:
    """poc_plan.md H4の5型から少なくとも2件ずつ、合計10件の模擬例外を生成する
    （決定的：乱数を使わないので毎回同じ10件になる。台帳スキーマ（`ledger.py`）に
    合わせたフィールドを持つが、`DiscrepancyLedger` には登録しない——EXP-10は
    実際の検知パイプラインの出力ではなく、評価者に提示する固定シナリオ集である）。
    """
    assert DISCREPANCY_TYPES == {
        "unscanned_move",
        "wrong_slot",
        "wrong_scan",
        "late_registration",
        "ghost_stock",
        "other",
    }
    templates = [
        (
            "unscanned_move",
            "Storage_Aで現物を目視確認",
            "記録上はStorage_Bのまま",
            "記録をStorage_Aに訂正し、未スキャン移動として台帳に確定登録する",
        ),
        (
            "wrong_slot",
            "指定と異なる棚段(rack2-3)に現物を確認",
            "棚割当はrack1-2のまま",
            "現物の実棚(rack2-3)に記録を訂正する",
        ),
        (
            "wrong_scan",
            "現物はケース42、スキャン対象はケース17だった形跡",
            "ケース17として登録",
            "スキャン対象をケース42に訂正し、ケース17の記録を取り消す",
        ),
        (
            "late_registration",
            "現物は45分前に検品済み",
            "登録は現物確認時刻の45分後",
            "記録時刻ではなく現物確認時刻を正として台帳に反映する",
        ),
        (
            "ghost_stock",
            "現物なし（棚は空）",
            "在庫あり(数量1)として記録",
            "在庫を0に訂正し、幽霊在庫として台帳に確定登録する",
        ),
    ]
    cases: list[MockCase] = []
    for i in range(10):
        disc_type, evidence, record, resolution = templates[i % len(templates)]
        severity = SEVERITIES_CYCLE[i % len(SEVERITIES_CYCLE)]
        cases.append(
            MockCase(
                case_id=f"exp10_case_{i + 1:02d}",
                object_id=f"gt:Pallet_{1000 + i:04d}",
                discrepancy_type=disc_type,
                severity=severity,
                physical_evidence_note=evidence,
                record_value=record,
                record_source_system="wms_mock",
                correct_resolution=resolution,
            )
        )
    return cases


SEVERITIES_CYCLE = tuple(sorted(SEVERITIES))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS resolutions (
    resolution_id TEXT PRIMARY KEY,
    evaluator_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    condition TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    resolution_given TEXT,
    is_correct INTEGER,
    correction_reflected INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS sus_responses (
    evaluator_id TEXT PRIMARY KEY,
    answers_json TEXT NOT NULL,
    sus_score REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


class Exp10Harness:
    """解決時間・正答率・訂正反映率の記録（SQLite）。`ui/app.py` の EXP-10 タブと
    テスト（直接関数呼び出し）の両方から使う共通実装。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def start_case(self, evaluator_id: str, case_id: str, condition: str, order_index: int) -> str:
        if condition not in CONDITIONS:
            raise ValueError(f"未知の condition: {condition}（{CONDITIONS} のいずれか）")
        resolution_id = f"{evaluator_id}_{case_id}_{condition}"
        self._conn.execute(
            "INSERT OR REPLACE INTO resolutions "
            "(resolution_id, evaluator_id, case_id, condition, order_index, started_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                resolution_id,
                evaluator_id,
                case_id,
                condition,
                order_index,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return resolution_id

    def finish_case(
        self,
        resolution_id: str,
        resolution_given: str,
        case: MockCase,
        correction_reflected: bool,
        notes: str = "",
    ) -> float:
        """解決を記録し、経過秒数を返す。正答判定は評価者の自己申告ではなく
        `case.correct_resolution` との一致で自動的に行う（poc_plan.md「正答率」）。"""
        row = self._conn.execute(
            "SELECT started_at FROM resolutions WHERE resolution_id=?", (resolution_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"未知の resolution_id: {resolution_id}（start_case が先に必要）")
        started_at = datetime.fromisoformat(row[0])
        ended_at = datetime.now(UTC)
        is_correct = resolution_given.strip() == case.correct_resolution.strip()
        self._conn.execute(
            "UPDATE resolutions SET ended_at=?, resolution_given=?, is_correct=?, "
            "correction_reflected=?, notes=? WHERE resolution_id=?",
            (
                ended_at.isoformat(),
                resolution_given,
                int(is_correct),
                int(correction_reflected),
                notes,
                resolution_id,
            ),
        )
        self._conn.commit()
        return (ended_at - started_at).total_seconds()

    def summary(self) -> dict[str, float]:
        """条件別の平均解決時間・正答率・訂正反映率（`resolution_time_reduction`
        等 H9 指標の計算に使う集計）。"""
        result: dict[str, float] = {}
        for condition in CONDITIONS:
            rows = self._conn.execute(
                "SELECT started_at, ended_at, is_correct, correction_reflected "
                "FROM resolutions WHERE condition=? AND ended_at IS NOT NULL",
                (condition,),
            ).fetchall()
            if not rows:
                continue
            durations = [
                (datetime.fromisoformat(e) - datetime.fromisoformat(s)).total_seconds()
                for s, e, _, _ in rows
            ]
            result[f"{condition}_mean_resolution_time_s"] = sum(durations) / len(durations)
            result[f"{condition}_correct_rate"] = sum(r[2] for r in rows) / len(rows)
            result[f"{condition}_correction_reflected_rate"] = sum(r[3] for r in rows) / len(rows)
            result[f"{condition}_n"] = float(len(rows))
        return result

    def record_sus(self, evaluator_id: str, answers: list[int]) -> float:
        score = compute_sus_score(answers)
        self._conn.execute(
            "INSERT OR REPLACE INTO sus_responses (evaluator_id, answers_json, sus_score, "
            "recorded_at) VALUES (?,?,?,?)",
            (evaluator_id, str(answers), score, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
        return score

    def close(self) -> None:
        self._conn.close()


def compute_sus_score(answers: list[int]) -> float:
    """標準SUS計算式。`answers` は10問・各1〜5のリッカート尺度回答。
    奇数問（1,3,5,7,9問目。0始まり添字では偶数インデックス）は (回答-1)、
    偶数問は (5-回答) を加算し、合計に2.5を掛けて0〜100のスコアにする。"""
    if len(answers) != 10:
        raise ValueError(f"SUSは10問固定（{len(answers)}件が渡された）")
    if any(a < 1 or a > 5 for a in answers):
        raise ValueError("各回答は1〜5の範囲でなければならない")
    total = 0
    for i, a in enumerate(answers):
        total += (a - 1) if i % 2 == 0 else (5 - a)
    return total * 2.5


__all__ = [
    "CONDITIONS",
    "SUS_QUESTIONS",
    "MockCase",
    "generate_mock_cases",
    "Exp10Harness",
    "compute_sus_score",
]
