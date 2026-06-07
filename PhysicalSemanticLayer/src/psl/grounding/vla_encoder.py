"""VLA-style grounding with MuJoCo rendering + CLIP embeddings (Phase D).

Uses mujoco.Renderer to produce RGB images of scene objects from
camera viewpoints, then encodes them with a frozen CLIP ViT-B/32
encoder to produce embedding vectors.

The embeddings are stored as Phytes with provenance tracking the
encoding source and model version.

If open_clip is not installed, falls back to a deterministic hash-based
pseudo-embedding that preserves the same API but without real vision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any  # MuJoCo model/data types are opaque C extensions

import numpy as np
from numpy.typing import NDArray

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry

# Embedding dimension (CLIP ViT-B/32 = 512)
EMBEDDING_DIM = 512

# CLIP model identifier used when real CLIP is active
_CLIP_MODEL_NAME = "ViT-B-32"
_CLIP_PRETRAINED = "laion2b_s34b_b79k"

# Zero-shot affordance text prompts
_AFFORDANCE_PROMPTS = [
    "a graspable object",
    "a non-graspable surface",
    "a detachable component",
    "a fixed structure",
    "made of metal",
    "made of plastic",
    "made of glass",
    "made of composite material",
]


@dataclass(frozen=True)
class AffordancePrediction:
    """Affordance prediction from the VLA encoder.

    Fields:
        graspable: Whether the object can be grasped.
        detachable: Whether the object can be detached/removed.
        material: Predicted material class.
        embedding: Raw CLIP embedding vector.
        confidence: Overall prediction confidence.
    """

    graspable: bool
    detachable: bool
    material: str
    embedding: NDArray[np.float64]
    confidence: float


class VLAEncoder:
    """VLA-style encoder: MuJoCo render -> CLIP embedding -> affordance prediction.

    If open_clip is not installed, uses deterministic hash-based pseudo-embeddings
    that preserve the API without requiring the vision model.

    Args:
        use_real_clip: If True, load the actual CLIP model (requires open_clip).
        seed: Random seed for reproducibility.
        sim_model: Optional mujoco.MjModel for auto-rendering (typed Any because
            MuJoCo types are opaque C extensions not available at import time).
        sim_data: Optional mujoco.MjData for auto-rendering (typed Any, same reason).
    """

    def __init__(
        self,
        use_real_clip: bool = False,
        seed: int = 42,
        sim_model: Any = None,  # mujoco.MjModel — opaque C extension type
        sim_data: Any = None,  # mujoco.MjData — opaque C extension type
    ) -> None:
        self._use_real_clip = use_real_clip
        self._rng = np.random.default_rng(seed)
        self._clip_model: object = None
        self._preprocess: object = None
        self._tokenizer: object = None
        self._sim_model = sim_model
        self._sim_data = sim_data

        if use_real_clip:
            try:
                import open_clip

                self._clip_model, _, self._preprocess = open_clip.create_model_and_transforms(
                    _CLIP_MODEL_NAME, pretrained=_CLIP_PRETRAINED
                )
                self._tokenizer = open_clip.get_tokenizer(_CLIP_MODEL_NAME)
                # Freeze the model — no gradient computation needed
                self._clip_model.eval()  # type: ignore[attr-defined]
            except ImportError:
                self._use_real_clip = False

    def encode_object(
        self,
        object_name: str,
        timestamp: float = 0.0,
        image: NDArray[np.uint8] | None = None,
    ) -> Phyte:
        """Encode an object into a Phyte with embedding as value.

        Args:
            object_name: Name of the object (used for hash-based fallback).
            timestamp: When this encoding was performed.
            image: RGB image from MuJoCo renderer (H, W, 3). Optional.
                   If use_real_clip=True and no image is provided but sim_model
                   and sim_data are available, an image is auto-rendered.

        Returns:
            Phyte with embedding vector as value, encoder provenance.
        """
        if self._use_real_clip and self._clip_model is not None:
            if image is None and self._sim_model is not None and self._sim_data is not None:
                image = render_object_image(self._sim_model, self._sim_data, body_name=object_name)
            if image is not None:
                embedding = self._encode_clip(image)
            else:
                embedding = self._encode_hash(object_name)
        else:
            embedding = self._encode_hash(object_name)

        prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source="vla_encoder:clip_vit_b32"
                    if self._use_real_clip
                    else "vla_encoder:hash_fallback",
                    operation=f"encode({object_name})",
                    timestamp=timestamp,
                )
            ],
            confidence=0.9 if self._use_real_clip else 0.7,
        )

        return Phyte(
            semantic_id=f"embedding:{object_name}",
            frame="world",
            pose=identity_se3(),
            timestamp=timestamp,
            clock_domain="sim",
            unit="dimensionless",
            value=embedding,
            covariance=np.eye(EMBEDDING_DIM) * (0.01 if self._use_real_clip else 0.1),
            provenance=prov,
        )

    def predict_affordances(
        self,
        object_name: str,
        timestamp: float = 0.0,
        image: NDArray[np.uint8] | None = None,
    ) -> AffordancePrediction:
        """Predict affordances for an object using its embedding.

        When real CLIP is available, uses zero-shot classification with
        text prompts. Otherwise falls back to hash-based prediction.

        Args:
            object_name: Name of the object.
            timestamp: When this prediction was made.
            image: Optional RGB image.

        Returns:
            AffordancePrediction with graspable, detachable, material, embedding.
        """
        embedding_phyte = self.encode_object(object_name, timestamp, image)
        embedding = embedding_phyte.value

        if self._use_real_clip and self._clip_model is not None and self._tokenizer is not None:
            return self._predict_affordances_clip(embedding, embedding_phyte)

        return self._predict_affordances_hash(embedding, embedding_phyte)

    def _predict_affordances_clip(
        self,
        embedding: NDArray[np.float64],
        embedding_phyte: Phyte,
    ) -> AffordancePrediction:
        """Zero-shot affordance prediction using CLIP text-image similarity."""
        import torch

        with torch.no_grad():
            tokens = self._tokenizer(_AFFORDANCE_PROMPTS)  # type: ignore[operator]
            text_features = self._clip_model.encode_text(tokens)  # type: ignore[attr-defined]
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)

            # Image embedding is already normalised; convert to tensor
            img_tensor = torch.tensor(embedding, dtype=torch.float32).unsqueeze(0)
            # Cosine similarity (embedding is already unit-norm)
            similarities = (img_tensor @ text_features.T).squeeze(0).cpu().numpy()

        # Interpret similarities
        graspable = float(similarities[0]) > float(similarities[1])
        detachable = float(similarities[2]) > float(similarities[3])

        # Material: best match among indices 4-7
        material_sims = similarities[4:8]
        material_names = ["metal", "plastic", "glass", "composite"]
        material = material_names[int(np.argmax(material_sims))]

        return AffordancePrediction(
            graspable=graspable,
            detachable=detachable,
            material=material,
            embedding=embedding,
            confidence=float(embedding_phyte.provenance.confidence),
        )

    def _predict_affordances_hash(
        self,
        embedding: NDArray[np.float64],
        embedding_phyte: Phyte,
    ) -> AffordancePrediction:
        """Hash-based affordance prediction (fallback, no external deps)."""
        hash_val = int(np.abs(embedding[:4].sum()) * 1000) % 100
        graspable = hash_val > 30  # ~70% graspable
        detachable = hash_val > 50  # ~50% detachable

        materials = ["pcb", "metal", "plastic", "glass", "composite"]
        material_idx = hash_val % len(materials)

        return AffordancePrediction(
            graspable=graspable,
            detachable=detachable,
            material=materials[material_idx],
            embedding=embedding,
            confidence=float(embedding_phyte.provenance.confidence),
        )

    def _encode_clip(self, image: NDArray[np.uint8]) -> NDArray[np.float64]:
        """Encode image with real CLIP model.

        Preprocesses the image using the CLIP transforms, runs it through
        the frozen vision encoder, and returns a 512-dim unit-normalised
        float64 embedding.
        """
        import torch
        from PIL import Image

        pil_image = Image.fromarray(image)
        preprocessed = self._preprocess(pil_image).unsqueeze(0)  # type: ignore[operator]

        with torch.no_grad():
            features = self._clip_model.encode_image(preprocessed)  # type: ignore[attr-defined]
            # Normalise to unit sphere
            features = features / features.norm(dim=-1, keepdim=True)

        embedding: NDArray[np.float64] = features.squeeze(0).cpu().numpy().astype(np.float64)
        return embedding

    def _encode_hash(self, object_name: str) -> NDArray[np.float64]:
        """Deterministic hash-based pseudo-embedding (fallback)."""
        # Use object name hash as seed for deterministic embedding
        name_hash = hash(object_name) & 0xFFFFFFFF
        rng = np.random.default_rng(name_hash)
        embedding = rng.standard_normal(EMBEDDING_DIM)
        # Normalize to unit sphere
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding


def render_object_image(
    sim_model: Any,  # mujoco.MjModel — opaque C extension type
    sim_data: Any,  # mujoco.MjData — opaque C extension type
    camera_name: str = "overhead_cam",
    width: int = 224,
    height: int = 224,
    body_name: str | None = None,
) -> NDArray[np.uint8] | None:
    """Render an RGB image from a MuJoCo camera.

    If *body_name* is given and a matching body exists in the model, the
    camera is automatically positioned to look at that body's centre of
    mass. This makes it possible to get a focused view of a specific
    object without requiring a pre-placed camera in the scene XML.

    Args:
        sim_model: mujoco.MjModel instance.
        sim_data: mujoco.MjData instance.
        camera_name: Name of camera in scene XML (used when body_name is None).
        width: Image width.
        height: Image height.
        body_name: Optional body name to point the camera toward.

    Returns:
        (H, W, 3) uint8 RGB array, or None if rendering fails.
    """
    try:
        import mujoco

        model: Any = sim_model
        data: Any = sim_data
        renderer = mujoco.Renderer(model, height, width)

        if body_name is not None:
            # Try to find the body and point the camera at it
            try:
                body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
                if body_id >= 0:
                    # Create a movable camera scene pointing at the body
                    cam = mujoco.MjvCamera()
                    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
                    cam.lookat[:] = data.xpos[body_id]
                    cam.distance = 0.6
                    cam.azimuth = 45.0
                    cam.elevation = -30.0
                    renderer.update_scene(data, camera=cam)
                else:
                    renderer.update_scene(data)
            except Exception:
                renderer.update_scene(data)
        else:
            renderer.update_scene(data)

        image = renderer.render()
        renderer.close()
        return np.array(image, dtype=np.uint8)
    except Exception:
        return None
