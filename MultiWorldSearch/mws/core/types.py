"""Shared enums and type aliases used across MWS."""

from __future__ import annotations

import enum


class CloudMode(enum.StrEnum):
    """Runtime cloud mode: mock (default, offline) or live (requires API key)."""

    MOCK = "mock"
    LIVE = "live"


class Modality(enum.StrEnum):
    """Data modality categories aligned with PROJECT.md §1.2."""

    POINTCLOUD = "pointcloud"
    RGBD = "rgbd"
    POSE = "pose"
    CONTACT = "contact"
    TELEMETRY = "telemetry"
    ACTION_TRAJECTORY = "action_trajectory"
    POLICY = "policy"
    AFFORDANCE = "affordance"
    STRUCTURED_RECORD = "structured_record"
    DOCUMENT = "document"
    SOP = "sop"
    SDS = "sds"
    TICKET = "ticket"
    SKILL_DEMO = "skill_demo"


class ConsumerType(enum.StrEnum):
    """Consumer types for projection (PROJECT.md §3 consumer-aware projection)."""

    VLA = "vla"
    LLM = "llm"
    CONTROL_LOOP = "control_loop"
    DASHBOARD = "dashboard"
    AUDIT = "audit"


class EntityKind(enum.StrEnum):
    """Kinds of entities in the 4D scene graph."""

    OBJECT = "object"
    PLACE = "place"
    AGENT = "agent"
    ZONE = "zone"


class RelationType(enum.StrEnum):
    """Relation types between scene graph entities."""

    SPATIAL_CONTAINS = "spatial_contains"
    SPATIAL_NEAR = "spatial_near"
    SEMANTIC_RELATED = "semantic_related"
    TEMPORAL_BEFORE = "temporal_before"
    TEMPORAL_DURING = "temporal_during"
    CAUSAL = "causal"
    PART_OF = "part_of"


class EmbeddingSpace(enum.StrEnum):
    """Identifies the embedding model+dims+version. Never mix spaces in one index."""

    MOCK_128 = "mock-128-v1"
    MOCK_768 = "mock-768-v1"
    GEMMA_128 = "embgemma-128-v1"
    GEMMA_768 = "embgemma-768-v1"
    MINILM_384 = "minilm-384-v1"
    GEMINI_768 = "gemini2-768-v1"
    # v2: asymmetric task-instruction prefixes (IMPROVEMENT E1). Same model,
    # different coordinate semantics — never compare v1 and v2 vectors.
    GEMINI_768_V2 = "gemini2-768-v2"
    GEMINI_3072 = "gemini2-3072-v1"
