"""VLA encoder tests — hash fallback always tested, CLIP marked @slow."""

from __future__ import annotations

import numpy as np
import pytest

from psl.grounding.vla_encoder import EMBEDDING_DIM, VLAEncoder


class TestHashFallback:
    """Tests for the hash-based fallback (no external deps needed)."""

    def test_encode_returns_phyte(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        p = enc.encode_object("blue_gear", timestamp=1.0)
        assert p.semantic_id == "embedding:blue_gear"
        assert p.value.shape == (EMBEDDING_DIM,)
        assert p.unit == "dimensionless"

    def test_embedding_unit_sphere(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        p = enc.encode_object("test_obj")
        norm = float(np.linalg.norm(p.value))
        assert abs(norm - 1.0) < 1e-6

    def test_deterministic(self) -> None:
        enc1 = VLAEncoder(use_real_clip=False, seed=42)
        enc2 = VLAEncoder(use_real_clip=False, seed=42)
        p1 = enc1.encode_object("widget")
        p2 = enc2.encode_object("widget")
        np.testing.assert_array_equal(p1.value, p2.value)

    def test_different_objects_differ(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        p1 = enc.encode_object("gear")
        p2 = enc.encode_object("bolt")
        assert not np.allclose(p1.value, p2.value)

    def test_predict_affordances(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        aff = enc.predict_affordances("metal_part")
        assert isinstance(aff.graspable, bool)
        assert isinstance(aff.detachable, bool)
        assert isinstance(aff.material, str)
        assert aff.embedding.shape == (EMBEDDING_DIM,)
        assert 0.0 <= aff.confidence <= 1.0

    def test_provenance_tracks_source(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        p = enc.encode_object("obj", timestamp=5.0)
        assert p.provenance.chain[0].source == "vla_encoder:hash_fallback"
        assert p.provenance.confidence == 0.7

    def test_graceful_fallback_when_no_clip(self) -> None:
        """use_real_clip=True but no torch/open_clip -> falls back."""
        enc = VLAEncoder(use_real_clip=True, seed=42)
        # Should still work via fallback
        p = enc.encode_object("test")
        assert p.value.shape == (EMBEDDING_DIM,)

    def test_get_vla_info_reflects_actual_mode(self) -> None:
        from eval.runner.manifest import get_vla_info

        enc_hash = VLAEncoder(use_real_clip=False, seed=42)
        info = get_vla_info(enc_hash)
        assert info["mode"] == "hash_fallback"

        # Without encoder arg, behavior depends on import availability
        info_default = get_vla_info()
        assert info_default["mode"] in ("clip", "hash_fallback")


@pytest.mark.metamorphic
class TestVLAMetamorphic:
    """Metamorphic: same input -> same output (determinism)."""

    def test_encoding_determinism(self) -> None:
        enc = VLAEncoder(use_real_clip=False, seed=42)
        e1 = enc.encode_object("x", timestamp=0.0)
        e2 = enc.encode_object("x", timestamp=0.0)
        np.testing.assert_array_equal(e1.value, e2.value)
