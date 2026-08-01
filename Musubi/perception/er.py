"""ER-backed perception (hybrid / live dials).

Gemini Robotics ER points (normalized [y,x], 0..1000) are lifted to world Claims via sim depth
(pixel→world). The ER call goes through the injected ``ERClient`` — in Musubi that is always the
``clients/`` VCR adapter (offline: FakeGeminiClient / cassettes). This module never imports Gemini.
"""

from __future__ import annotations

from clients.er import ERClient, ERPoint  # ER interface lives in clients (PER→CLI, §6.2)
from core.ids import mint
from ontology.generated.musubi_types import Claim, ClaimKind, Method, Realm
from perception.pixel_world import normalized_yx_to_pixel, unproject
from sim.render import SceneRenderer
from sim.world import World

__all__ = ["ERPerception", "ERClient", "ERPoint"]

_SENSOR = "msb:sensor/er_cam"


class ERPerception:
    """Turns ER points into world-anchored Claims using the sim renderer's depth."""

    def __init__(
        self, world: World, er_client: ERClient, renderer: SceneRenderer | None = None
    ) -> None:
        self._world = world
        self._client = er_client
        self._renderer = renderer or SceneRenderer(world)
        self._n = 0

    def detect(self, camera: str = "overhead", query: str = "point to every pallet") -> list[Claim]:
        rgb = self._renderer.rgb(camera)
        depth = self._renderer.depth(camera)
        h, w = depth.shape
        claims: list[Claim] = []
        for point in self._client.detect_points(rgb, query):
            row, col = normalized_yx_to_pixel(point.y, point.x, h, w)
            d = float(depth[row, col])
            wx, wy, wz = unproject(self._world, camera, row, col, d, h, w)
            self._n += 1
            claims.append(
                Claim(
                    iri=mint("claim", "er", f"{self._n}"),
                    claimKind=ClaimKind.position,
                    subject=mint("tracked", f"er{self._n}"),  # identity resolved via anchors later
                    predicate="position",
                    objectValue=f"{wx:.4f},{wy:.4f},{wz:.4f}",
                    confidence=float(point.confidence),
                    realm=Realm.real,
                    method=Method.sensor_fusion,
                    source=_SENSOR,
                )
            )
        return claims
