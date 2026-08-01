"""Oracle perception — offline detections from god-view ground truth + calibrated synthetic noise.

The oracle dial lets us study the semantic layer *without* the network or ER, while still
resembling real perception error: position noise, dropout, false positives, and tag-read errors are
drawn from the seeded ``oracle_noise`` sub-stream with magnitudes from ``config/registry.yaml``.
Identified detections (tag readable) get the entity IRI; unidentified ones get an anonymous
tracked-object IRI (to be bound later by IdentityBinding).
"""

from __future__ import annotations

from config import load_registry
from core.ids import mint
from ontology.generated.musubi_types import Claim, ClaimKind, Method, Realm
from sim.world import World

_SENSOR = "msb:sensor/oracle_cam"


def _pose_value(x: float, y: float, z: float) -> str:
    return f"{x:.4f},{y:.4f},{z:.4f}"


class OraclePerception:
    """Produces position Claims for visible entities with calibrated noise (deterministic)."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._rng = world.rng.generator("oracle_noise")
        reg = load_registry()
        self._sigma = float(reg.require("perception.oracle_noise.position_sigma_m"))
        self._dropout = float(reg.require("perception.oracle_noise.dropout_prob"))
        self._false_pos = float(reg.require("perception.oracle_noise.false_positive_prob"))
        self._tag_err = float(reg.require("perception.oracle_noise.tag_read_error_prob"))
        self._n = 0

    def detect(self) -> list[Claim]:
        """One perception pass: a list of position Claims about currently-visible entities."""
        claims: list[Claim] = []
        truth = self._world.ground_truth()
        for state in truth.values():
            if not state.visible:
                continue
            if self._rng.random() < self._dropout:
                continue  # missed detection
            nx = state.position[0] + float(self._rng.normal(0.0, self._sigma))
            ny = state.position[1] + float(self._rng.normal(0.0, self._sigma))
            nz = state.position[2]
            identified = state.tagged and self._world.tag_readable(state.body)
            if identified and self._rng.random() < self._tag_err:
                identified = False  # tag misread
            subject = state.iri if identified else mint("tracked", f"{self._n}")
            self._n += 1
            confidence = 0.97 if identified else 0.7
            claims.append(
                Claim(
                    iri=mint("claim", "oracle", f"{self._n}"),
                    claimKind=ClaimKind.position,
                    subject=subject,
                    predicate="position",
                    objectValue=_pose_value(nx, ny, nz),
                    confidence=confidence,
                    realm=Realm.real,
                    method=Method.direct_measurement,
                    source=_SENSOR,
                )
            )
        # spurious false positive
        if self._false_pos > 0.0 and self._rng.random() < self._false_pos:
            self._n += 1
            fx, fy = self._rng.uniform(-2.0, 2.0, size=2)
            claims.append(
                Claim(
                    iri=mint("claim", "oracle", f"{self._n}"),
                    claimKind=ClaimKind.presence,
                    subject=mint("tracked", f"fp{self._n}"),
                    predicate="position",
                    objectValue=_pose_value(float(fx), float(fy), 0.06),
                    confidence=0.4,
                    realm=Realm.real,
                    method=Method.direct_measurement,
                    source=_SENSOR,
                )
            )
        return claims

    def detected_entities(self) -> set[str]:
        """Entity IRIs identified in one detection pass (for scoring vs ground truth)."""
        return {str(c.subject) for c in self.detect() if str(c.subject).startswith("msb:entity/")}
