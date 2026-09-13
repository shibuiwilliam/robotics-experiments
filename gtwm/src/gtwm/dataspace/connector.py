"""コネクタのコアロジック（ADR-0002）：ポリシー検査＋監査ログ＋在庫（交換対象）の払い出し。

HTTP に依存しない（`server.py` が薄い HTTP ラッパーとしてこれを呼ぶ、テスト・実験からは
このモジュールを直接呼べる）。1拠点＝1 `Connector` インスタンス。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from gtwm.dataspace.audit import AuditEntry, AuditLog
from gtwm.dataspace.models import (
    ExchangeBelief,
    ExchangeRequest,
    ExchangeResponse,
    PredictionRecord,
)
from gtwm.dataspace.policy import OdrlPolicy, PolicyViolation, check_purpose, retention_cutoff


@dataclass
class Connector:
    """1拠点のコネクタ：ローカルの信念・予測を、ポリシーに従って他拠点に払い出す。"""

    site_id: str
    policy: OdrlPolicy
    audit: AuditLog
    beliefs: list[ExchangeBelief] = field(default_factory=list)
    predictions: list[PredictionRecord] = field(default_factory=list)

    def publish_belief(self, item: ExchangeBelief) -> None:
        self.beliefs.append(item)

    def publish_prediction(self, item: PredictionRecord) -> None:
        self.predictions.append(item)

    def handle_request(self, req: ExchangeRequest, now: datetime) -> ExchangeResponse:
        """交換要求を処理する。ポリシー違反は必ず拒否し、監査ログに残す。"""
        try:
            check_purpose(self.policy, req.purpose)
        except PolicyViolation as exc:
            self.audit.record(
                AuditEntry(
                    requested_at=now,
                    requester_site=req.requester_site,
                    purpose=req.purpose,
                    item_type=req.item_type,
                    granted=False,
                    reason=str(exc),
                    n_items=0,
                )
            )
            return ExchangeResponse(granted=False, reason=str(exc), items=[])

        cutoff = retention_cutoff(self.policy, now)
        items: list[ExchangeBelief] | list[PredictionRecord]
        if req.item_type == "belief":
            items = [
                b for b in self.beliefs if b.valid_from >= cutoff and b.valid_from >= req.since
            ]
        else:
            items = [
                p
                for p in self.predictions
                if p.provenance.generated_at >= cutoff and p.provenance.generated_at >= req.since
            ]

        self.audit.record(
            AuditEntry(
                requested_at=now,
                requester_site=req.requester_site,
                purpose=req.purpose,
                item_type=req.item_type,
                granted=True,
                reason="許可",
                n_items=len(items),
            )
        )
        return ExchangeResponse(granted=True, reason="許可", items=items)  # type: ignore[arg-type]
