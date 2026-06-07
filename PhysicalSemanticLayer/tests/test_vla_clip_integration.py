"""Integration test for real CLIP encoder path.

Marked @pytest.mark.slow — only runs when open-clip-torch is installed.
"""

from __future__ import annotations

import numpy as np
import pytest

from psl.grounding.vla_encoder import EMBEDDING_DIM, VLAEncoder


def _clip_available() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _clip_available(), reason="open-clip-torch not installed")
class TestRealCLIP:
    def test_encode_with_real_clip(self) -> None:
        enc = VLAEncoder(use_real_clip=True, seed=42)
        assert enc._use_real_clip
        image = np.random.default_rng(42).integers(0, 255, (224, 224, 3), dtype=np.uint8)
        p = enc.encode_object("test_object", timestamp=1.0, image=image)
        assert p.value.shape == (EMBEDDING_DIM,)
        assert abs(float(np.linalg.norm(p.value)) - 1.0) < 1e-5
        assert "clip_vit_b32" in p.provenance.chain[0].source

    def test_clip_deterministic(self) -> None:
        enc = VLAEncoder(use_real_clip=True, seed=42)
        image = np.random.default_rng(42).integers(0, 255, (224, 224, 3), dtype=np.uint8)
        p1 = enc.encode_object("obj", image=image)
        p2 = enc.encode_object("obj", image=image)
        np.testing.assert_array_almost_equal(p1.value, p2.value, decimal=5)

    def test_clip_affordance_prediction(self) -> None:
        enc = VLAEncoder(use_real_clip=True, seed=42)
        image = np.random.default_rng(42).integers(0, 255, (224, 224, 3), dtype=np.uint8)
        aff = enc.predict_affordances("metal_bracket", image=image)
        assert isinstance(aff.graspable, bool)
        assert isinstance(aff.material, str)
        assert aff.material in ("metal", "plastic", "glass", "composite")
        assert aff.confidence > 0

    def test_clip_vs_hash_differ(self) -> None:
        enc_clip = VLAEncoder(use_real_clip=True, seed=42)
        enc_hash = VLAEncoder(use_real_clip=False, seed=42)
        image = np.random.default_rng(42).integers(0, 255, (224, 224, 3), dtype=np.uint8)
        p_clip = enc_clip.encode_object("gear", image=image)
        p_hash = enc_hash.encode_object("gear")
        assert not np.allclose(p_clip.value, p_hash.value)
