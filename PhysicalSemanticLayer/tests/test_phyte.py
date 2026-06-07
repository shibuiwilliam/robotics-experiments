"""Unit tests for Phyte construction, validation, and serialization."""

from __future__ import annotations

import numpy as np
import pytest

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3, rotation_z, se3_distance
from psl.phyte.provenance import Provenance
from psl.phyte.units import are_compatible, convert_quantity, parse_unit


@pytest.mark.unit
class TestPhyteConstruction:
    def test_minimal_phyte(self) -> None:
        p = Phyte(
            semantic_id="test",
            frame="world",
            timestamp=0.0,
            unit="m",
            value=np.array([1.0]),
            covariance=np.array([[0.01]]),
        )
        assert p.semantic_id == "test"
        assert p.frame == "world"
        assert p.dimensionality == "[length]"
        assert p.value[0] == 1.0

    def test_auto_dimensionality(self) -> None:
        p = Phyte(
            semantic_id="angle",
            frame="world",
            timestamp=0.0,
            unit="rad",
            value=np.array([0.5]),
            covariance=np.array([[0.001]]),
        )
        assert p.dimensionality == "dimensionless"  # rad is dimensionless in pint

    def test_covariance_shape_validation(self) -> None:
        with pytest.raises(ValueError, match="Covariance shape"):
            Phyte(
                semantic_id="bad",
                frame="world",
                timestamp=0.0,
                unit="m",
                value=np.array([1.0, 2.0, 3.0]),
                covariance=np.array([[0.01]]),  # Wrong shape for 3D value
            )

    def test_pose_shape_validation(self) -> None:
        with pytest.raises(ValueError, match="pose must be 4"):
            Phyte(
                semantic_id="bad",
                frame="world",
                pose=np.eye(3),  # Should be 4x4
                timestamp=0.0,
                unit="m",
                value=np.array([1.0]),
                covariance=np.array([[0.01]]),
            )

    def test_frozen(self) -> None:
        p = Phyte(
            semantic_id="test",
            frame="world",
            timestamp=0.0,
            unit="m",
            value=np.array([1.0]),
            covariance=np.array([[0.01]]),
        )
        with pytest.raises(Exception):  # noqa: B017
            p.semantic_id = "modified"  # type: ignore[misc]

    def test_with_frame(self) -> None:
        p = Phyte(
            semantic_id="test",
            frame="world",
            timestamp=0.0,
            unit="m",
            value=np.array([1.0]),
            covariance=np.array([[0.01]]),
        )
        R = rotation_z(0.5)
        new_pose = make_se3(R, np.array([1.0, 0.0, 0.0]))
        p2 = p.with_frame("robot_base", new_pose)
        assert p2.frame == "robot_base"
        assert p.frame == "world"  # Original unchanged

    def test_with_provenance(self) -> None:
        p = Phyte(
            semantic_id="test",
            frame="world",
            timestamp=0.0,
            unit="m",
            value=np.array([1.0]),
            covariance=np.array([[0.01]]),
        )
        p2 = p.with_provenance("adapter", "transform", 1.0, 0.95)
        assert len(p2.provenance.chain) == 1
        assert p2.provenance.confidence == pytest.approx(0.95)
        assert len(p.provenance.chain) == 0  # Original unchanged


@pytest.mark.unit
class TestGeometry:
    def test_identity_distance(self) -> None:
        eye = identity_se3()
        assert se3_distance(eye, eye) == pytest.approx(0.0, abs=1e-12)

    def test_pure_translation(self) -> None:
        T1 = identity_se3()
        T2 = make_se3(np.eye(3), np.array([1.0, 0.0, 0.0]))
        assert se3_distance(T1, T2) == pytest.approx(1.0, abs=1e-10)

    def test_pure_rotation(self) -> None:
        T1 = identity_se3()
        R = rotation_z(np.pi / 4)
        T2 = make_se3(R, np.zeros(3))
        assert se3_distance(T1, T2) == pytest.approx(np.pi / 4, abs=1e-10)

    def test_random_se3_deterministic(self) -> None:
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)
        from psl.phyte.geometry import random_se3

        T1 = random_se3(rng1)
        T2 = random_se3(rng2)
        assert se3_distance(T1, T2) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.unit
class TestUnits:
    def test_parse_unit(self) -> None:
        u = parse_unit("m")
        assert str(u) == "m"

    def test_compatible(self) -> None:
        assert are_compatible(parse_unit("m"), parse_unit("mm"))
        assert not are_compatible(parse_unit("m"), parse_unit("rad"))

    def test_convert(self) -> None:
        result = convert_quantity(1.0, parse_unit("m"), parse_unit("mm"))
        assert result == pytest.approx(1000.0)


@pytest.mark.unit
class TestProvenance:
    def test_extend(self) -> None:
        p = Provenance()
        p2 = p.extend("src", "op", 1.0, 0.9)
        assert len(p2.chain) == 1
        assert p2.confidence == pytest.approx(0.9)
        assert len(p.chain) == 0  # Immutable

    def test_chain_confidence_decay(self) -> None:
        p = Provenance()
        p = p.extend("a", "op1", 1.0, 0.9)
        p = p.extend("b", "op2", 2.0, 0.9)
        assert p.confidence == pytest.approx(0.81)
        assert len(p.chain) == 2
