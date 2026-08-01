"""The perception dial — oracle | hybrid | live (PROJECT.md §8).

Separates semantic-layer experiments from perception performance. ``oracle`` is offline (ground
truth + calibrated noise); ``hybrid``/``live`` route through ER (behind the VCR). The default dial
lives in ``config/registry.yaml`` (G1 sets the real default after E0.5).
"""

from __future__ import annotations

from enum import StrEnum

from config import load_registry
from ontology.generated.musubi_types import Claim
from perception.er import ERClient, ERPerception
from perception.oracle import OraclePerception
from sim.render import SceneRenderer
from sim.world import World


class Dial(StrEnum):
    oracle = "oracle"
    hybrid = "hybrid"
    live = "live"


def default_dial() -> Dial:
    return Dial(load_registry().require("perception.default_dial"))


class Perception:
    """Dial-driven perception facade. ``detect()`` returns position Claims for the current world."""

    def __init__(
        self,
        world: World,
        dial: Dial | str | None = None,
        *,
        er_client: ERClient | None = None,
        renderer: SceneRenderer | None = None,
    ) -> None:
        self._world = world
        self._dial = Dial(dial) if dial is not None else default_dial()
        if self._dial is Dial.oracle:
            self._backend: OraclePerception | ERPerception = OraclePerception(world)
        else:
            if er_client is None:
                raise ValueError(
                    f"dial {self._dial.value!r} requires an er_client (behind clients/)"
                )
            self._backend = ERPerception(world, er_client, renderer)

    @property
    def dial(self) -> Dial:
        return self._dial

    def detect(self) -> list[Claim]:
        return self._backend.detect()
