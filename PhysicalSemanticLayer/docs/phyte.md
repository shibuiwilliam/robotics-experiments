# Phyte Schema Specification

> Phyte = **Phy**sical Seman**t**ic Grounded Data Unit — PSL's canonical self-describing data object.

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `semantic_id` | `str` | Ontology-linked identifier (e.g., `joint_position_0`, `end_effector_pose`) |
| `frame` | `str` | Reference frame name (e.g., `world`, `robot_base`) |
| `pose` | `NDArray[float64]` (4x4) | SE(3) homogeneous matrix — pose in `frame` |
| `timestamp` | `float` | Time value in the clock domain |
| `clock_domain` | `str` | Clock source identifier (e.g., `sim`, `wall`) |
| `time_uncertainty` | `float` (>= 0) | Uncertainty on timestamp (seconds) |
| `unit` | `str` | Physical unit, pint-parseable (e.g., `rad`, `m`, `N*m`) |
| `dimensionality` | `str` | Auto-derived from unit (e.g., `[length]`, `dimensionless`) |
| `value` | `NDArray[float64]` | Numeric payload (scalar or array) |
| `covariance` | `NDArray[float64]` | Covariance matrix matching value shape (NxN for N-dim value) |
| `provenance` | `Provenance` | Source chain + confidence |

## Invariants

1. **No bare floats**: Every physical quantity must be wrapped in a Phyte.
2. **Unit consistency**: `dimensionality` is auto-derived from `unit` at construction.
3. **Covariance shape**: Must be (N, N) where N = len(value).
4. **Pose shape**: Must be (4, 4) SE(3) matrix.
5. **Immutability**: Phyte is frozen after construction — use `with_*` methods for copies.

## Provenance

Each Phyte carries a `Provenance` with:
- `chain`: ordered list of `ProvenanceEntry` (source, operation, timestamp)
- `confidence`: [0, 1] score, decays multiplicatively through the chain

## Implementation

- Located at `src/psl/phyte/core.py`
- Built on Pydantic v2 with `model_validator` for construction-time checks
- SE(3) geometry via `pytransform3d` (wrapped in `src/psl/phyte/geometry.py`)
- Units via `pint` (wrapped in `src/psl/phyte/units.py`)
