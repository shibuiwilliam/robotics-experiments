"""Perception dial (oracle | hybrid | live). Turns sim ground truth / ER output into
DetectedObject Claims; lifts ER normalized [y,x] points to world coordinates via sim depth
(pixel->world). ER calls themselves go through ``clients/`` (never Gemini directly).
"""

from __future__ import annotations
