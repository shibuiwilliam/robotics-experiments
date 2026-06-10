"""VLA-style grounding — embedding, affordance, and action prediction."""

from psl.grounding.vla_encoder import (
    ActionPrediction,
    AffordancePrediction,
    VLAEncoder,
    render_object_image,
)

__all__ = [
    "ActionPrediction",
    "AffordancePrediction",
    "VLAEncoder",
    "render_object_image",
]

# SmolVLAEncoder is optional — requires lerobot
try:
    from psl.grounding.smolvla_encoder import SmolVLAEncoder  # noqa: F401

    __all__.append("SmolVLAEncoder")
except ImportError:
    pass
