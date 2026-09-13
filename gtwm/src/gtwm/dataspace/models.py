"""拠点間交換用のデータモデル（C12、poc_plan.md 5.5、ADR-0002）。

交換単位は `gt:Belief` と予測（値・予測区間・ホライズン・モデル版）に PROV-O の出所を
付与したものだけ。`model_config = ConfigDict(extra="forbid")` により、生映像・潜在表現
フィールドを後から付け足すことを型レベルで禁止する（poc_plan.md 5.5「生映像・潜在表現は
交換しない」を型で強制するため。tests/unit/test_dataspace_models.py で検証）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Provenance(BaseModel):
    """PROV-O の出所情報（`prov:wasGeneratedBy` / `wasAttributedTo` / `generatedAtTime`）。"""

    model_config = ConfigDict(extra="forbid")

    generated_by: str  # prov:wasGeneratedBy（例：gt:WM, gt:ProbeAlpha）
    attributed_to: str  # prov:wasAttributedTo（発信拠点、例：site_b）
    generated_at: datetime  # prov:generatedAtTime


class ExchangeBelief(BaseModel):
    """交換可能な信念1件。`kg.schema.Belief` の交換用サブセット（RDF 変換はしない）。"""

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str
    confidence: float
    valid_from: datetime
    provenance: Provenance


class PredictionRecord(BaseModel):
    """交換可能な予測1件：値・予測区間・ホライズン・モデル版（poc_plan.md 5.5）。"""

    model_config = ConfigDict(extra="forbid")

    variable: str
    point_estimate: float
    interval_low: float
    interval_high: float
    horizon_s: float
    model_version: str
    provenance: Provenance
    # 受領側 ECE 評価用（PoC の簡略化）：本来は受領側が後から自分の観測で正誤を確認するが、
    # このスモーク実装では発信側が同梱する「後から判明した正誤」をそのまま使う。実運用では
    # 受領側が独自に確認するまでの遅延・不確実性を別途モデル化する必要がある（要フォローアップ）。
    outcome_correct: bool | None = None


ExchangeItem = ExchangeBelief | PredictionRecord
ItemType = Literal["belief", "prediction"]


class ExchangeRequest(BaseModel):
    """交換要求：どの拠点が・どの目的で・何を要求するか（ODRL の目的限定を検査する対象）。"""

    model_config = ConfigDict(extra="forbid")

    requester_site: str
    purpose: str
    item_type: ItemType
    since: datetime


class ExchangeResponse(BaseModel):
    """交換応答：許可されたか、理由、許可された場合のみ items を含む。"""

    model_config = ConfigDict(extra="forbid")

    granted: bool
    reason: str
    items: list[ExchangeBelief] | list[PredictionRecord] = []
