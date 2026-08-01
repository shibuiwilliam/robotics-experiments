from __future__ import annotations

import re
import sys
from datetime import (
    date,
    datetime,
    time
)
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Literal,
    Optional,
    Union
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer
)


metamodel_version = "1.11.0"
version = "0.1.0"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias = True,
        validate_by_name = True,
        validate_assignment = True,
        validate_default = True,
        extra = "forbid",
        arbitrary_types_allowed = True,
        use_enum_values = True,
        strict = False,
    )





class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key:str):
        return getattr(self.root, key)

    def __getitem__(self, key:str):
        return self.root[key]

    def __setitem__(self, key:str, value):
        self.root[key] = value

    def __contains__(self, key:str) -> bool:
        return key in self.root


linkml_meta = LinkMLMeta({'default_prefix': 'msb',
     'default_range': 'string',
     'description': 'The single source of meaning for Musubi — the shared ontology '
                    'binding the physical world (robots), the cognitive world '
                    '(agents), and the business world (external systems). '
                    'Concepts, relations, and actions per PROJECT.md §8 and the 14 '
                    'mechanisms of the Scenario Catalog §4.1. Every schema / SHACL '
                    'shape / vocabulary / type is generated from THIS source '
                    '(CLAUDE.md §0-4); it is never hand-written elsewhere. '
                    'Grounded in W3C SOSA/SSN, PROV-O, OWL-Time, QUDT, GS1 EPCIS.',
     'id': 'https://musubi.dev/ontology/musubi',
     'imports': ['linkml:types'],
     'license': 'MIT',
     'name': 'musubi',
     'prefixes': {'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'msb': {'prefix_prefix': 'msb',
                          'prefix_reference': 'https://musubi.dev/ontology/musubi/'},
                  'prov': {'prefix_prefix': 'prov',
                           'prefix_reference': 'http://www.w3.org/ns/prov#'},
                  'qudt': {'prefix_prefix': 'qudt',
                           'prefix_reference': 'http://qudt.org/schema/qudt/'},
                  'skos': {'prefix_prefix': 'skos',
                           'prefix_reference': 'http://www.w3.org/2004/02/skos/core#'},
                  'sosa': {'prefix_prefix': 'sosa',
                           'prefix_reference': 'http://www.w3.org/ns/sosa/'},
                  'time': {'prefix_prefix': 'time',
                           'prefix_reference': 'http://www.w3.org/2006/time#'},
                  'xsd': {'prefix_prefix': 'xsd',
                          'prefix_reference': 'http://www.w3.org/2001/XMLSchema#'}},
     'source_file': '/Users/shibuiyusuke/tmp/robotics-experiments/Musubi/ontology/src/musubi.yaml',
     'title': 'Musubi Core Ontology'} )

class Realm(str, Enum):
    """
    The world a Claim belongs to. All realms coexist in one graph and are diff-queryable; only `real` describes the actual world (PROJECT.md §8: Realm).
    """
    real = "real"
    """
    The actual physical/business world.
    """
    simulated = "simulated"
    """
    A simulation twin's world (e.g. the morning-standup twin, F3).
    """
    planned = "planned"
    """
    An intended future state asserted by a plan.
    """
    hypothetical = "hypothetical"
    """
    A counterfactual or what-if state.
    """


class AspectKind(str, Enum):
    """
    The three faces of an Entity (three-phase aspect model).
    """
    physical = "physical"
    """
    PhysicalAspect — the observed face (pose, appearance, tags).
    """
    cognitive = "cognitive"
    """
    CognitiveAspect — the inferred face (derived beliefs).
    """
    business = "business"
    """
    BusinessAspect — the ledger face (SKU, lot, ownership, quantity).
    """


class AnchorKind(str, Enum):
    """
    A kind of identity cue bundled to ground an Entity's identity.
    """
    physical = "physical"
    """
    Tag read or appearance embedding.
    """
    spatial = "spatial"
    """
    A position-plus-time cue.
    """
    business_key = "business_key"
    """
    A business key such as SKU or serial.
    """


class Method(str, Enum):
    """
    How a Claim was produced. Feeds belief mediation as a tie-breaker rank (config/registry.yaml:claims.method_rank).
    """
    direct_measurement = "direct_measurement"
    """
    A direct sensor measurement.
    """
    sensor_fusion = "sensor_fusion"
    """
    Fusion of multiple sensors.
    """
    ledger_of_record = "ledger_of_record"
    """
    The authoritative business system of record.
    """
    inference = "inference"
    """
    Derived by reasoning.
    """
    hearsay = "hearsay"
    """
    Reported by another party, unverified.
    """


class ClaimKind(str, Enum):
    """
    The subject-kind of a Claim; selects its confidence-decay half-life.
    """
    position = "position"
    """
    Where something is.
    """
    pose = "pose"
    """
    Position + orientation.
    """
    presence = "presence"
    """
    Whether something is present.
    """
    ownership = "ownership"
    """
    Who owns / is responsible for something.
    """
    tag_read = "tag_read"
    """
    A tag/label read.
    """
    quantity = "quantity"
    """
    A counted quantity.
    """
    state = "state"
    """
    A qualitative state (e.g. damaged).
    """
    custody = "custody"
    """
    Who currently holds custody.
    """


class ReversibilityClass(str, Enum):
    """
    How undoable an Action is. Drives autonomy and approval requirements — irreversible actions require explicit approval (PROJECT.md §8: ReversibilityClass).
    """
    reversible = "reversible"
    """
    Fully undoable at negligible cost.
    """
    compensable = "compensable"
    """
    Not directly undoable but a compensating action exists (saga).
    """
    irreversible = "irreversible"
    """
    Cannot be undone (e.g. disposal). Requires approval gate.
    """


class RealizationKind(str, Enum):
    """
    How an abstract Action is realized (realization polymorphism).
    """
    physical = "physical"
    """
    PhysicalRealization — a robot skill.
    """
    informational = "informational"
    """
    InformationalRealization — an API/business step.
    """
    human = "human"
    """
    HumanRealization — a person, treated as a first-class realization.
    """


class NormModality(str, Enum):
    """
    Deontic modality of a Norm.
    """
    obligation = "obligation"
    """
    Must do.
    """
    permission = "permission"
    """
    May do.
    """
    prohibition = "prohibition"
    """
    Must not do.
    """


class NormStrength(str, Enum):
    """
    Whether a Norm is a hard constraint or a soft preference.
    """
    hard = "hard"
    """
    Inviolable (e.g. the privacy floor, safety separation).
    """
    soft = "soft"
    """
    A preference tradeable against others.
    """


class ExceptionDisposition(str, Enum):
    """
    The escalation path for an Exception (deviation from expectation).
    """
    retry = "retry"
    """
    Retry the action.
    """
    replan = "replan"
    """
    Re-plan around the deviation.
    """
    redelegate = "redelegate"
    """
    Change the delegation.
    """
    human = "human"
    """
    Escalate to a human.
    """



class Entity(ConfiguredBaseModel):
    """
    Anything with identity (an immutable IRI): objects, people, places, equipment, organizations, information objects. Its three aspects are an achievement, not a given.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    aspects: Optional[list[Aspect]] = Field(default=None, description="""The Entity's aspects (physical/cognitive/business projections).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity']} })
    anchorBundle: Optional[str] = Field(default=None, description="""The bundle of identity cues for this Entity.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity']} })


class Aspect(ConfiguredBaseModel):
    """
    A projection (face) of an Entity. Concrete subtypes are the three aspect kinds.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'abstract': True, 'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    kind: AspectKind = Field(default=..., description="""Which aspect kind.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })
    authority: Optional[str] = Field(default=None, description="""The Authority (system of record) for this aspect × scope.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })


class PhysicalAspect(Aspect):
    """
    The observed face — pose, appearance, tags. Authority is typically a sensor/perception.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    kind: AspectKind = Field(default=..., description="""Which aspect kind.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })
    authority: Optional[str] = Field(default=None, description="""The Authority (system of record) for this aspect × scope.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })


class CognitiveAspect(Aspect):
    """
    The inferred face — derived beliefs about the Entity.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    kind: AspectKind = Field(default=..., description="""Which aspect kind.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })
    authority: Optional[str] = Field(default=None, description="""The Authority (system of record) for this aspect × scope.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })


class BusinessAspect(Aspect):
    """
    The ledger face — SKU, lot, ownership, quantity. Authority is a business system.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    kind: AspectKind = Field(default=..., description="""Which aspect kind.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })
    authority: Optional[str] = Field(default=None, description="""The Authority (system of record) for this aspect × scope.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Aspect']} })


class Authority(ConfiguredBaseModel):
    """
    A declared System of Record for an aspect × scope. There is NO global master of an Entity; authority is per-aspect (PROJECT.md §8: Authority). Reputation may be dynamic (S9).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    aspectKind: AspectKind = Field(default=..., description="""The aspect this authority is authoritative for.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Authority']} })
    scope: Optional[str] = Field(default=None, description="""The scope (zone/domain) of authority.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Authority', 'Norm']} })
    effectiveConfidence: Optional[float] = Field(default=None, description="""Dynamic credit rating of this source (S9 — reputation from track record).""", ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Authority']} })


class Anchor(ConfiguredBaseModel):
    """
    One identity cue. Bundled (not used alone) to ground identity.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'abstract': True, 'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    anchorKind: AnchorKind = Field(default=..., description="""The kind of cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })
    value: Optional[str] = Field(default=None, description="""The cue value (tag id, business key, or serialized embedding ref).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })


class PhysicalAnchor(Anchor):
    """
    A tag read or appearance-embedding cue.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    anchorKind: AnchorKind = Field(default=..., description="""The kind of cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })
    value: Optional[str] = Field(default=None, description="""The cue value (tag id, business key, or serialized embedding ref).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })


class SpatialAnchor(Anchor):
    """
    A position-plus-time cue.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    anchorKind: AnchorKind = Field(default=..., description="""The kind of cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })
    value: Optional[str] = Field(default=None, description="""The cue value (tag id, business key, or serialized embedding ref).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })


class BusinessKeyAnchor(Anchor):
    """
    A business key (SKU / serial / lot).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    anchorKind: AnchorKind = Field(default=..., description="""The kind of cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })
    value: Optional[str] = Field(default=None, description="""The cue value (tag id, business key, or serialized embedding ref).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Anchor']} })


class AnchorBundle(ConfiguredBaseModel):
    """
    The bundle of identity cues (physical / spatial / business-key) held together to support gradual grounding of identity (PROJECT.md §8: Anchor).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    anchors: Optional[list[Anchor]] = Field(default=None, description="""The bundled anchors.""", json_schema_extra = { "linkml_meta": {'domain_of': ['AnchorBundle']} })


class IdentityBinding(ConfiguredBaseModel):
    """
    A confidence-and-evidence-bearing, RETRACTABLE binding of an observation (TrackedObject) to an Entity. NEVER created for persons (NFR-P privacy floor — anonymous location/pose only).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    confidence: Optional[float] = Field(default=None, description="""Degree of belief in a Claim, 0..1 at validTime (pre-decay).""", ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding', 'Claim']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    validTime: Optional[float] = Field(default=None, description="""When the asserted fact holds in the world (bitemporal — OWL-Time / PROV).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding', 'Claim', 'Event']} })
    trackedObject: str = Field(default=..., description="""IRI of the observation being bound.""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding']} })
    entity: str = Field(default=..., description="""IRI of the Entity bound to.""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding']} })
    evidence: Optional[list[str]] = Field(default=None, description="""IRIs of the anchors/Claims justifying the binding.""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding']} })
    retracted: Optional[bool] = Field(default=False, description="""Whether this binding has been retracted.""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding'], 'ifabsent': 'boolean(false)'} })
    isPerson: Optional[bool] = Field(default=False, description="""Must be false. A binding for a person violates the privacy floor (NFR-P) and is rejected by the world_ok shape.""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding'], 'ifabsent': 'boolean(false)'} })


class Claim(ConfiguredBaseModel):
    """
    An assertion about world state. Never a bare fact — always someone's claim, carrying source, method, confidence, valid/transaction time, and realm. Append-only: replaced via `supersedes`, never deleted (PROJECT.md §8: Claim; CLAUDE.md §0-3).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    source: Optional[str] = Field(default=None, description="""IRI of the asserting actor/sensor/system (provenance).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    method: Optional[Method] = Field(default=None, description="""How the Claim was produced.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    confidence: Optional[float] = Field(default=None, description="""Degree of belief in a Claim, 0..1 at validTime (pre-decay).""", ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding', 'Claim']} })
    realm: Optional[Realm] = Field(default=None, description="""The Realm a Claim belongs to.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'Plan']} })
    validTime: Optional[float] = Field(default=None, description="""When the asserted fact holds in the world (bitemporal — OWL-Time / PROV).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding', 'Claim', 'Event']} })
    transactionTime: Optional[float] = Field(default=None, description="""When the record was written to the store (bitemporal).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    supersedes: Optional[str] = Field(default=None, description="""IRI of the Claim this one replaces (append-only; losers are never deleted).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    claimKind: ClaimKind = Field(default=..., description="""The subject-kind (selects decay half-life).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    subject: str = Field(default=..., description="""IRI of the Entity/aspect the Claim is about.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    predicate: Optional[str] = Field(default=None, description="""The asserted property (e.g. position, ownership).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'Goal']} })
    objectValue: Optional[str] = Field(default=None, description="""The asserted value (serialized; may reference a QuantityValue/Pose).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    justifiedBy: Optional[list[str]] = Field(default=None, description="""IRIs of upstream Claims/norms/business grounds (accountability chain — NFR-TRACE).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })


class MediationResult(ConfiguredBaseModel):
    """
    The adjudication of conflicting Claims by authority × decay-adjusted confidence × method rank. The loser is superseded, not deleted (belief mediation, PROJECT.md §8).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    winner: str = Field(default=..., description="""IRI of the winning Claim.""", json_schema_extra = { "linkml_meta": {'domain_of': ['MediationResult']} })
    losers: Optional[list[str]] = Field(default=None, description="""IRIs of superseded Claims.""", json_schema_extra = { "linkml_meta": {'domain_of': ['MediationResult']} })
    reason: Optional[str] = Field(default=None, description="""Why the winner won (authority/confidence/method trace).""", json_schema_extra = { "linkml_meta": {'domain_of': ['MediationResult']} })


class QuantityValue(ConfiguredBaseModel):
    """
    A magnitude with a unit (QUDT). No bare numbers (design principle P1).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    magnitude: float = Field(default=..., description="""The numeric magnitude.""", json_schema_extra = { "linkml_meta": {'domain_of': ['QuantityValue']} })
    unit: str = Field(default=..., description="""The QUDT unit IRI/curie.""", json_schema_extra = { "linkml_meta": {'domain_of': ['QuantityValue']} })


class Frame(ConfiguredBaseModel):
    """
    A coordinate frame. Transforms between frames are themselves Claims.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })


class Pose(ConfiguredBaseModel):
    """
    A position (+ optional orientation) expressed in a named Frame. No bare coordinates.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    frame: str = Field(default=..., description="""The Frame the coordinates are in.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    x: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    y: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    z: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    qw: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    qx: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    qy: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })
    qz: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Pose']} })


class Zone(ConfiguredBaseModel):
    """
    A space carrying meaning and norms (e.g. quarantine zone, corridor, charging bay).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    norms: Optional[list[str]] = Field(default=None, description="""IRIs of Norms in force in this zone.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Zone']} })


class Route(ConfiguredBaseModel):
    """
    A connection between Zones.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    fromZone: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Route']} })
    toZone: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Route']} })


class Affordance(ConfiguredBaseModel):
    """
    An action possibility an object offers (the bridge between perception and action).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The abstract action this affordance enables.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })


class Action(ConfiguredBaseModel):
    """
    The abstraction of a world-changing act, with preconditions, expected effects, failure modes, compensation, reversibility class, cost, and resource requirements (unified action model).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The abstract action type (matched against capabilities).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })
    preconditions: Optional[list[str]] = Field(default=None, description="""Preconditions (SHACL-checkable references).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    expectedEffects: Optional[list[str]] = Field(default=None, description="""Expected effects.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    failureModes: Optional[list[str]] = Field(default=None, description="""Known failure modes.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    reversibility: ReversibilityClass = Field(default=..., description="""Reversibility class.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    compensation: Optional[str] = Field(default=None, description="""IRI of the compensating Action (if compensable).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    cost: Optional[QuantityValue] = Field(default=None, description="""Estimated cost.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    requirement: Optional[Requirement] = Field(default=None, description="""Capability requirement to execute this action.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action', 'Task']} })


class Skill(Action):
    """
    A robot-world realization of an Action (PhysicalRealization).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The abstract action type (matched against capabilities).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })
    preconditions: Optional[list[str]] = Field(default=None, description="""Preconditions (SHACL-checkable references).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    expectedEffects: Optional[list[str]] = Field(default=None, description="""Expected effects.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    failureModes: Optional[list[str]] = Field(default=None, description="""Known failure modes.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    reversibility: ReversibilityClass = Field(default=..., description="""Reversibility class.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    compensation: Optional[str] = Field(default=None, description="""IRI of the compensating Action (if compensable).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    cost: Optional[QuantityValue] = Field(default=None, description="""Estimated cost.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    requirement: Optional[Requirement] = Field(default=None, description="""Capability requirement to execute this action.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action', 'Task']} })


class Tool(Action):
    """
    An agent-world realization of an Action (an ADK FunctionTool / InformationalRealization).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The abstract action type (matched against capabilities).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })
    preconditions: Optional[list[str]] = Field(default=None, description="""Preconditions (SHACL-checkable references).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    expectedEffects: Optional[list[str]] = Field(default=None, description="""Expected effects.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    failureModes: Optional[list[str]] = Field(default=None, description="""Known failure modes.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    reversibility: ReversibilityClass = Field(default=..., description="""Reversibility class.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    compensation: Optional[str] = Field(default=None, description="""IRI of the compensating Action (if compensable).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    cost: Optional[QuantityValue] = Field(default=None, description="""Estimated cost.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    requirement: Optional[Requirement] = Field(default=None, description="""Capability requirement to execute this action.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action', 'Task']} })


class WorkflowStep(Action):
    """
    A business-world realization of an Action (InformationalRealization).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The abstract action type (matched against capabilities).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })
    preconditions: Optional[list[str]] = Field(default=None, description="""Preconditions (SHACL-checkable references).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    expectedEffects: Optional[list[str]] = Field(default=None, description="""Expected effects.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    failureModes: Optional[list[str]] = Field(default=None, description="""Known failure modes.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    reversibility: ReversibilityClass = Field(default=..., description="""Reversibility class.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    compensation: Optional[str] = Field(default=None, description="""IRI of the compensating Action (if compensable).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    cost: Optional[QuantityValue] = Field(default=None, description="""Estimated cost.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action']} })
    requirement: Optional[Requirement] = Field(default=None, description="""Capability requirement to execute this action.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action', 'Task']} })


class Realization(ConfiguredBaseModel):
    """
    A concrete realization of an abstract Action in one world (realization polymorphism).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    realizationKind: RealizationKind = Field(default=..., description="""physical/informational/human.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Realization']} })
    action: str = Field(default=..., description="""IRI of the abstract Action realized.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Realization']} })
    actor: str = Field(default=..., description="""IRI of the actor (robot/agent/person) realizing it.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Realization', 'Capability']} })


class Compensation(ConfiguredBaseModel):
    """
    A compensating action pairing (for compensable actions / sagas).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    forAction: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Compensation']} })
    compensatingAction: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Compensation']} })


class Saga(ConfiguredBaseModel):
    """
    A compensation-based distributed execution (physical saga) — an ordered set of steps.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    steps: Optional[list[str]] = Field(default=None, description="""Ordered Action IRIs.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Saga', 'Plan']} })
    compensations: Optional[list[Compensation]] = Field(default=None, description="""Compensations registered as steps commit.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Saga']} })


class Capability(ConfiguredBaseModel):
    """
    A capacity an actor advertises. Matched to Requirements by subsumption (not string equality).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actor: str = Field(default=..., description="""IRI of the advertising actor.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Realization', 'Capability']} })
    actionTypes: list[str] = Field(default=..., description="""Abstract action types the actor can perform.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Capability']} })
    qos: Optional[list[QuantityValue]] = Field(default=None, description="""Quality-of-service (e.g. max payload, accuracy). Measured QoS may override advertised (S9/C3).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Capability']} })
    certified: Optional[bool] = Field(default=False, description="""Whether the capability is conformance-certified.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Capability'], 'ifabsent': 'boolean(false)'} })


class Requirement(ConfiguredBaseModel):
    """
    A task's demand, matched against advertised Capabilities by subsumption.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    actionType: str = Field(default=..., description="""The required abstract action type.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Affordance', 'Action', 'Requirement']} })
    qosConstraints: Optional[list[QuantityValue]] = Field(default=None, description="""Required QoS constraints.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement']} })


class Norm(ConfiguredBaseModel):
    """
    An obligation / permission / prohibition with scope, strength (hard/soft), and a link to its source document (normSource). Compiled from documents; checked at plan- and run-time.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    modality: NormModality = Field(default=..., description="""obligation/permission/prohibition.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Norm']} })
    strength: NormStrength = Field(default=..., description="""hard/soft.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Norm']} })
    scope: Optional[list[str]] = Field(default=None, description="""Where/when the norm applies (zone/regime/time).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Authority', 'Norm']} })
    normSource: Optional[str] = Field(default=None, description="""IRI of the source clause/document (RAG-citable).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Norm']} })
    regime: Optional[str] = Field(default=None, description="""Optional regime this norm belongs to (e.g. emergency overlay, C2).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Norm']} })
    priority: Optional[int] = Field(default=0, description="""Overlay priority (higher overrides lower in a regime switch).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Norm'], 'ifabsent': 'int(0)'} })


class Delegation(ConfiguredBaseModel):
    """
    A delegation of authority/work, carrying a capability token. Chains form the Accountability Chain; recorded alongside any ADK internal call (CLAUDE.md §8).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    delegator: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Delegation']} })
    delegate: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Delegation']} })
    capabilityToken: Optional[str] = Field(default=None, description="""Scoped token authorizing the delegate.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Delegation']} })
    task: Optional[str] = Field(default=None, description="""IRI of the delegated Task.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Delegation']} })


class Contract(ConfiguredBaseModel):
    """
    An agreement between actors (chains into the accountability chain).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    parties: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Contract']} })
    terms: Optional[str] = Field(default=None, description="""Contract terms (e.g. unit price, SLA).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Contract']} })


class Goal(ConfiguredBaseModel):
    """
    A declarative goal.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    predicate: Optional[str] = Field(default=None, description="""The desired world condition.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'Goal']} })


class Task(ConfiguredBaseModel):
    """
    Assignable work derived from a Goal.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    goal: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Task', 'Plan']} })
    requirement: Optional[Requirement] = Field(default=None, description="""Capability requirement to perform the task.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Action', 'Task']} })
    assignedTo: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Task']} })


class Plan(ConfiguredBaseModel):
    """
    An ordered composition of Actions to achieve a Goal. Gated before execution.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    goal: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Task', 'Plan']} })
    steps: Optional[list[str]] = Field(default=None, description="""Ordered Action IRIs.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Saga', 'Plan']} })
    realm: Optional[Realm] = Field(default=Realm.planned, description="""Realm of the plan (usually planned).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'Plan'], 'ifabsent': 'Realm(planned)'} })


class Event(ConfiguredBaseModel):
    """
    A meaningful state change (the base of narrative aggregation).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    validTime: Optional[float] = Field(default=None, description="""When the asserted fact holds in the world (bitemporal — OWL-Time / PROV).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding', 'Claim', 'Event']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    eventType: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Event']} })
    about: Optional[list[str]] = Field(default=None, description="""IRI(s) the event concerns.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Event', 'Exception']} })


class Episode(ConfiguredBaseModel):
    """
    One purposeful activity (a run of events).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    events: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Episode']} })


class Case(ConfiguredBaseModel):
    """
    A business-unit narrative. Its IRI is the trace key threading order->motor for semantic observability (NFR-OBS).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    episodes: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Case']} })


class Exception(ConfiguredBaseModel):
    """
    A deviation from expectation and its handling path (a domain concept on the bus, not a crash).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://musubi.dev/ontology/musubi'})

    iri: str = Field(default=..., description="""Immutable global identity of an Entity (PROJECT.md §8: instances referenced by IRI).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Aspect',
                       'Authority',
                       'Anchor',
                       'AnchorBundle',
                       'IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Realization',
                       'Compensation',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Delegation',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    label: Optional[str] = Field(default=None, description="""Human-readable label. Never an identity cue.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Entity',
                       'Authority',
                       'Frame',
                       'Zone',
                       'Route',
                       'Affordance',
                       'Action',
                       'Saga',
                       'Capability',
                       'Requirement',
                       'Norm',
                       'Contract',
                       'Goal',
                       'Task',
                       'Plan',
                       'Event',
                       'Episode',
                       'Case',
                       'Exception']} })
    createdAt: Optional[float] = Field(default=None, description="""Sim-clock time (seconds since run epoch) the record was created (transactionTime source).""", json_schema_extra = { "linkml_meta": {'domain_of': ['IdentityBinding',
                       'Claim',
                       'MediationResult',
                       'Delegation',
                       'Event',
                       'Exception']} })
    deviation: str = Field(default=..., description="""What deviated.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Exception']} })
    disposition: ExceptionDisposition = Field(default=..., description="""The chosen escalation path.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Exception']} })
    about: Optional[str] = Field(default=None, description="""IRI the exception concerns.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Event', 'Exception']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Entity.model_rebuild()
Aspect.model_rebuild()
PhysicalAspect.model_rebuild()
CognitiveAspect.model_rebuild()
BusinessAspect.model_rebuild()
Authority.model_rebuild()
Anchor.model_rebuild()
PhysicalAnchor.model_rebuild()
SpatialAnchor.model_rebuild()
BusinessKeyAnchor.model_rebuild()
AnchorBundle.model_rebuild()
IdentityBinding.model_rebuild()
Claim.model_rebuild()
MediationResult.model_rebuild()
QuantityValue.model_rebuild()
Frame.model_rebuild()
Pose.model_rebuild()
Zone.model_rebuild()
Route.model_rebuild()
Affordance.model_rebuild()
Action.model_rebuild()
Skill.model_rebuild()
Tool.model_rebuild()
WorkflowStep.model_rebuild()
Realization.model_rebuild()
Compensation.model_rebuild()
Saga.model_rebuild()
Capability.model_rebuild()
Requirement.model_rebuild()
Norm.model_rebuild()
Delegation.model_rebuild()
Contract.model_rebuild()
Goal.model_rebuild()
Task.model_rebuild()
Plan.model_rebuild()
Event.model_rebuild()
Episode.model_rebuild()
Case.model_rebuild()
Exception.model_rebuild()
