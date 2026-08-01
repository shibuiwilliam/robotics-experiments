"""Perception dial (oracle | hybrid | live). Turns sim ground truth / ER output into
DetectedObject Claims; lifts ER normalized [y,x] points to world coordinates via sim depth
(pixel->world). ER calls themselves go through ``clients/`` (never Gemini directly).
"""

from __future__ import annotations

from perception.dial import Dial, Perception, default_dial
from perception.er import ERClient, ERPerception, ERPoint
from perception.oracle import OraclePerception
from perception.pixel_world import normalized_yx_to_pixel, unproject

__all__ = [
    "Dial",
    "Perception",
    "default_dial",
    "OraclePerception",
    "ERPerception",
    "ERClient",
    "ERPoint",
    "normalized_yx_to_pixel",
    "unproject",
]
