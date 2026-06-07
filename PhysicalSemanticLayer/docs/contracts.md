# Fidelity Contracts

> Every translation edge declares what it preserves, what it loses, and how uncertainty grows.

## Contract Structure

| Field | Type | Description |
|-------|------|-------------|
| `adapter_id` | `str` | Which adapter/transform this contract belongs to |
| `preserved_fields` | `list[str]` | Phyte fields that survive losslessly |
| `lost_fields` | `list[str]` | Phyte fields dropped or degraded |
| `uncertainty_delta` | `float` (>= 0) | Expected covariance norm increase per translation |
| `information_loss_estimate` | `float` [0, 1] | Estimated mutual information loss |
| `notes` | `str` | Human-readable description of lossy aspects |

## Composition

Contracts compose via `compose()`:
- **Preserved** = intersection of both contracts' preserved fields
- **Lost** = union of both contracts' lost fields
- **Uncertainty delta** = sum
- **Information loss** = sum (clamped to 1.0)

## Verification (Oracle)

The `contract_accuracy` metric (eval/metrics/contract.py) compares:
- **Declared loss**: `information_loss_estimate` from the contract
- **Actual loss**: measured via `round_trip_information_loss` against ground truth

A contract is well-calibrated when `|declared - actual| < threshold`.

## Implementation

- Located at `src/psl/contracts/core.py`
- Each adapter exposes a `fidelity_contract` property
