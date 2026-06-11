"""Tests for the privileged-oracle perception-tax evaluation (H3, M7)."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality
from mws.eval.oracle import oracle_ranking, perception_tax


def _atoms(n: int) -> list[ExperienceAtom]:
    out = []
    for i in range(n):
        out.append(
            ExperienceAtom(
                modality=Modality.TELEMETRY,
                coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=float(i)),
                text_summary=f"atom {i}",
            )
        )
    return out


def test_oracle_ranks_all_relevant_first() -> None:
    atoms = _atoms(10)
    relevant = {atoms[3].atom_id, atoms[7].atom_id}
    ranking = oracle_ranking(atoms, relevant)
    assert set(ranking[:2]) == relevant
    assert len(ranking) == 10


def test_perception_tax_nonnegative_and_zero_for_oracle() -> None:
    atoms = _atoms(20)
    relevant = {a.atom_id for a in atoms[:5]}
    # A weak pipeline that found only 2 of 5 relevant in its top results.
    weak_pipeline = {
        "recall_at_5": 0.4,
        "recall_at_10": 0.4,
        "mrr": 0.5,
        "ndcg_at_5": 0.5,
        "ndcg_at_10": 0.5,
        "n_relevant": 5.0,
        "n_retrieved": 10.0,
    }
    result = perception_tax(atoms, relevant, weak_pipeline)
    assert result["oracle"]["recall_at_5"] == 1.0  # oracle = upper bound
    assert all(v >= 0.0 for k, v in result["tax"].items() if "recall" in k or "mrr" in k)
    assert result["tax"]["recall_at_10"] == 0.6

    # A pipeline that IS the oracle pays zero tax.
    zero = perception_tax(atoms, relevant, dict(result["oracle"]))
    assert all(abs(v) < 1e-9 for v in zero["tax"].values())
