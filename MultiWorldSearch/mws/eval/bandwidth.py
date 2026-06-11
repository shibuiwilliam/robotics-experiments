"""Virtual edge<->cloud bandwidth meter.

Tracks the bytes that would be sent to/from the cloud in a real edge deployment.
In mock mode, measures hypothetical payload sizes for embedding requests and
LLM calls to quantify the network cost of the current architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mws.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BandwidthRecord:
    """A single bandwidth measurement."""

    direction: str  # "upload" or "download"
    component: str  # "embedding", "llm", etc.
    bytes_count: int
    #: True when an actual cloud round-trip happened; False for cost-model
    #: (virtual) traffic. Mirrors the real/modeled LLM split (IMPROVEMENT M14).
    real: bool = False


@dataclass
class BandwidthMeter:
    """Tracks virtual edge<->cloud bandwidth usage."""

    records: list[BandwidthRecord] = field(default_factory=list)

    def record_upload(self, component: str, payload_bytes: int, *, real: bool = False) -> None:
        """Record bytes sent from edge to cloud."""
        self.records.append(
            BandwidthRecord(
                direction="upload", component=component, bytes_count=payload_bytes, real=real
            )
        )

    def record_download(self, component: str, payload_bytes: int, *, real: bool = False) -> None:
        """Record bytes received from cloud to edge."""
        self.records.append(
            BandwidthRecord(
                direction="download", component=component, bytes_count=payload_bytes, real=real
            )
        )

    def record_embedding_request(
        self, text: str, embedding_dims: int, *, real: bool = False
    ) -> None:
        """Record a hypothetical embedding API round-trip.

        Upload: text payload (~4 bytes per char UTF-8 avg for JSON).
        Download: float32 vector (4 bytes per dim) + overhead.
        """
        upload_bytes = len(text.encode("utf-8")) + 200  # JSON overhead
        download_bytes = embedding_dims * 4 + 200  # float32 vector + JSON overhead
        self.record_upload("embedding", upload_bytes, real=real)
        self.record_download("embedding", download_bytes, real=real)

    def record_llm_request(
        self, prompt_chars: int, response_chars: int, *, real: bool = False
    ) -> None:
        """Record an LLM API round-trip (real when an actual call happened)."""
        self.record_upload("llm", prompt_chars * 4 + 500, real=real)
        self.record_download("llm", response_chars * 4 + 200, real=real)

    def summary(self) -> dict[str, int | float]:
        """Get bandwidth summary."""
        total_upload = sum(r.bytes_count for r in self.records if r.direction == "upload")
        total_download = sum(r.bytes_count for r in self.records if r.direction == "download")

        by_component: dict[str, int] = {}
        for r in self.records:
            key = f"{r.direction}_{r.component}_bytes"
            by_component[key] = by_component.get(key, 0) + r.bytes_count

        total_real = sum(r.bytes_count for r in self.records if r.real)
        return {
            "total_upload_bytes": total_upload,
            "total_download_bytes": total_download,
            "total_bandwidth_bytes": total_upload + total_download,
            "total_real_bytes": total_real,
            "total_virtual_bytes": total_upload + total_download - total_real,
            "n_requests": len(self.records) // 2,  # each request = upload + download
            **by_component,
        }
