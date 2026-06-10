# Scenario 5: Hospital Pharmaceutical Logistics

## Business Story

A hospital pharmacy system manages controlled substances and prescription drugs. A
business agent enforces regulatory rules (schedule class, expiry dates, dosage limits)
while a robotic arm retrieves vials from a storage shelf. The system must detect when a
vial's symbolic identity (barcode claim: "Morphine 10mg") conflicts with the physical
observation. Additionally, a malicious or erroneous provenance injection attempts to
corrupt the custody chain — the system must fail safe, not fail open.

## Why This Scenario Matters

This is the **safety-critical** scenario. It tests two properties that cannot be
verified with standard data translation:

1. **Neuro-symbolic binding (RQ7):** A learned grounding system might say "this vial
   looks like Drug X" with 90% confidence. A symbolic system says "the barcode reads
   Drug Y." When these conflict, pure learned grounding would accept the wrong identity.
   PSL's neuro-symbolic binding requires BOTH signals to agree — the symbolic check
   catches what the embedding alone might miss. This scenario injects 4 claim/observation
   mismatches among 20 vials and verifies 100% detection.

2. **Provenance poisoning resilience:** An attacker or faulty sensor injects a Phyte with
   `provenance.confidence=0.1` and `timestamp=-1.0`. The safety gate's causal ordering
   check catches the timestamp violation (new timestamp 1.0 < previous timestamp 5.0 in
   the same clock domain). The low confidence is also flagged. The system fails safe —
   it rejects the poisoned data rather than incorporating it.

## What Actually Runs

| Component | What happens |
|-----------|-------------|
| **MuJoCo** | Loads `sim/scenes/s5_pharma_logistics/scene.xml` (Panda arm + storage shelf with 3 slots + 4 vials in different colors). Task controller: home → shelf approach → vial pick → inspect pose. |
| **Neuro-symbolic binding** | 20 vials simulated. 4 have intentional claim/observation mismatches (drug_0 claimed, drug_1 observed). Symbolic exact-match check detects all 4 → 100% detection rate, 0% false accepts. |
| **Provenance poisoning** | A Phyte is created with `Provenance(confidence=0.1, chain=[source="UNKNOWN", timestamp=-1.0])`. Safety gate `check()` is called with this as the new state and a normal Phyte (timestamp=5.0) as previous. The causal ordering check catches `1.0 < 5.0` → gate rejects. |
| **Claude Agent SDK** | Agent calls 4 MCP tools: `resolve_document_to_physical`, `query_world_model`, `subscribe_affordances`, `ToolSearch`. Cost: $0.094, 5 turns, 34.7s. |

## Breakpoints and Metrics

| Breakpoint | Value | Threshold | Result |
|-----------|-------|-----------|--------|
| Neuro-symbolic false accept | 0.0 | ≤ 0.05 | **PASS** |
| Provenance poisoning detected | 1.0 | ≥ 0.5 | **PASS** |

| Metric | Value |
|--------|-------|
| NS detection rate | 100% (4/4 mismatches caught) |
| Gate caught poisoning | Yes (causal violation) |
| Agent tool calls | 4 |
| Agent cost | $0.094 |

## RQ/Hypothesis Contributions

- **RQ7 (Safety):** Gate catches causal ordering violation from poisoned provenance.
  Neuro-symbolic binding achieves 0% false accept rate.

## Files

| Purpose | Path |
|---------|------|
| MuJoCo scene | `sim/scenes/s5_pharma_logistics/scene.xml` |
| Eval module | `eval/scenarios/s5_pharma_logistics/eval.py` |
| Config | `experiments/scenarios/s5_pharma_logistics.yaml` |
| Pseudo-cloud | `pseudo_cloud/s5_pharma_logistics/data.py` (formulary, expiry, audit_log) |
| Tests | `tests/scenarios/test_all_scenarios.py::TestS5PharmaLogistics` |

## Architecture Diagram

```mermaid
graph TB
    subgraph Cloud["Pseudo-Cloud"]
        Form["Formulary<br/>drug class + storage"]
        Expiry["Expiry DB<br/>days remaining"]
        Audit["Audit Log<br/>(generated)"]
    end

    subgraph Robots["MuJoCo Scene"]
        Arm["Panda Arm<br/>vial handling"]
        Vials["Vials<br/>V-001 to V-004"]
    end

    subgraph PSL_Core["PSL"]
        PA["PandaAdapter"]
        IR["Canonical IR"]
        WM["World Model"]
        Gate["Safety Gate<br/>causal ordering"]
        NS["Neuro-Symbolic<br/>CLIP + symbolic check"]
        Prov["Provenance<br/>poisoning detection"]
    end

    Agent["Claude Agent<br/>regulatory worker"]

    Form --> Agent
    Expiry --> Agent
    Agent -->|log| Audit

    Arm --> PA --> IR --> WM --> Gate
    NS -->|embedding vs label| WM
    Prov -->|integrity check| Gate

    Agent -->|resolve_document| Cloud
    Agent -->|subscribe_affordances| NS
```

## Process Workflow

```mermaid
sequenceDiagram
    participant Agent as Claude Agent
    participant PSL as PSL
    participant Gate as Safety Gate
    participant NS as Neuro-Symbolic Check
    participant Prov as Provenance

    Agent->>PSL: resolve_document("bin", "Bin_C")
    PSL-->>Agent: vial position

    Agent->>PSL: query_world_model("panda_arm")
    PSL-->>Agent: arm state with provenance

    Agent->>NS: subscribe_affordances("vial")
    NS->>NS: CLIP embedding says "glass vial"
    NS->>NS: Label claims "Morphine 10mg"
    NS-->>Agent: match=true, confidence=0.9

    Note over PSL,Gate: Provenance poisoning test
    PSL->>Prov: inject poisoned provenance (timestamp=-1.0)
    Prov->>Gate: causal ordering check
    Gate-->>PSL: REJECTED (timestamp violation)
    PSL-->>Agent: poisoning detected ✓

    Note over NS: False accept rate = 0.0<br/>(4/20 mismatches all caught)
```
