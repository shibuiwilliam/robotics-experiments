import pytest

from orx.common import iri


def test_namespaces_distinct() -> None:
    names = {iri.upper("X"), iri.cap("X"), iri.st("X"), iri.prov("X"), iri.biz("X")}
    assert len(names) == 5
    for n in names:
        assert n.startswith("https://orx.local/onto/")


def test_entity_roundtrip() -> None:
    e = iri.entity("box", "b-01")
    assert e == "https://orx.local/id/box/b-01"
    assert iri.parse_entity(e) == ("box", "b-01")


def test_invalid_term_rejected() -> None:
    with pytest.raises(ValueError):
        iri.upper("has space")
    with pytest.raises(ValueError):
        iri.entity("box", "../escape")


def test_claim_graph_iri() -> None:
    assert iri.claim("abc123") == "https://orx.local/id/claim/abc123"
