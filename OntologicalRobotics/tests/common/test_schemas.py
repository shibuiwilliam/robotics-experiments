import pytest
from pydantic import ValidationError

from orx.common import iri
from orx.common.schemas import Claim, PerceptionEvent, Term


def make_claim(**overrides: object) -> Claim:
    base: dict = dict(
        claim_id="c1",
        subject=iri.entity("box", "b1"),
        predicate=iri.st("inZone"),
        object=Term(kind="iri", value=iri.entity("zone", "shelf_a")),
        asserted_by=iri.entity("agent", "anchoring"),
        confidence=0.9,
        observed_at=1.5,
    )
    base.update(overrides)
    return Claim(**base)


def test_claim_roundtrip() -> None:
    c = make_claim()
    assert Claim.model_validate_json(c.model_dump_json()) == c


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        make_claim(confidence=1.5)


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        PerceptionEvent(
            event_id="e1", robot_id="r1", sim_time=0.0, detections=[], bogus=1
        )


def test_term_canonical() -> None:
    assert Term(kind="iri", value="https://x/y").canonical() == "<https://x/y>"
    assert Term(kind="literal", value="abc").canonical() == '"abc"'
    lit = Term(kind="literal", value="1.5", datatype=iri.XSD_DOUBLE)
    assert lit.canonical() == f'"1.5"^^<{iri.XSD_DOUBLE}>'
