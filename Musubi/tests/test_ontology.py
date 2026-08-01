"""P1 ontology tests — generated artifacts exist, JSON-Schema validates instances, and the
SHACL world_ok acceptance shapes catch the invariant-floor violations (privacy floor, claim
sanity). These are the dual-representation checks (structure + constraints).
"""

from __future__ import annotations

import pytest

from ontology import artifacts


def test_generated_artifacts_present() -> None:
    for path in (
        artifacts.JSON_SCHEMA_PATH,
        artifacts.CONTEXT_PATH,
        artifacts.SHACL_PATH,
        artifacts.WORLD_OK_PATH,
    ):
        assert path.exists(), f"missing {path} — run `make gen`"


def test_core_concepts_present_in_schema() -> None:
    """The ~30 core concepts (PROJECT.md §8) must all be in the generated schema."""
    defs = artifacts.json_schema()["$defs"]
    for concept in [
        "Entity",
        "Aspect",
        "Authority",
        "AnchorBundle",
        "IdentityBinding",
        "Claim",
        "MediationResult",
        "Action",
        "Skill",
        "Tool",
        "WorkflowStep",
        "Realization",
        "Capability",
        "Requirement",
        "Norm",
        "Delegation",
        "Contract",
        "Goal",
        "Task",
        "Plan",
        "Frame",
        "Zone",
        "Route",
        "Affordance",
        "Event",
        "Episode",
        "Case",
        "Exception",
        "QuantityValue",
        "Pose",
    ]:
        assert concept in defs, f"{concept} missing from generated JSON Schema"


def test_valid_claim_instance_passes_json_schema() -> None:
    claim = {
        "iri": "msb:claim/1",
        "claimKind": "position",
        "subject": "msb:entity/pallet-1",
        "source": "msb:sensor/cam-0",
        "method": "direct_measurement",
        "confidence": 0.9,
        "realm": "real",
        "validTime": 12.0,
        "transactionTime": 12.0,
    }
    artifacts.validate_instance(claim, "Claim")
    assert artifacts.instance_is_valid(claim, "Claim")


def test_invalid_claim_confidence_fails_json_schema() -> None:
    bad = {
        "iri": "msb:claim/2",
        "claimKind": "position",
        "subject": "msb:entity/pallet-1",
        "confidence": 1.7,  # out of [0,1]
        "realm": "real",
    }
    assert not artifacts.instance_is_valid(bad, "Claim")


def test_bad_enum_value_fails_json_schema() -> None:
    bad = {
        "iri": "msb:claim/3",
        "claimKind": "position",
        "subject": "msb:e/1",
        "realm": "elsewhere",  # not a Realm
    }
    assert not artifacts.instance_is_valid(bad, "Claim")


@pytest.mark.slow
def test_world_ok_accepts_clean_world() -> None:
    doc = [
        {
            "@id": "msb:claim/ok",
            "@type": "Claim",
            "claimKind": "position",
            "subject": {"@id": "msb:e/1"},
            "confidence": 0.8,
            "realm": "real",
        },
        {
            "@id": "msb:bind/ok",
            "@type": "IdentityBinding",
            "trackedObject": {"@id": "msb:obs/1"},
            "entity": {"@id": "msb:e/1"},
            "isPerson": False,
        },
    ]
    conforms, report = artifacts.validate_world(doc)
    assert conforms, report


@pytest.mark.slow
def test_world_ok_rejects_person_binding() -> None:
    """Privacy floor: binding a person must fail world_ok (NFR-P)."""
    doc = [
        {
            "@id": "msb:bind/person",
            "@type": "IdentityBinding",
            "trackedObject": {"@id": "msb:obs/9"},
            "entity": {"@id": "msb:person/9"},
            "isPerson": True,
        }
    ]
    conforms, report = artifacts.validate_world(doc)
    assert not conforms
    assert "Privacy floor" in report
