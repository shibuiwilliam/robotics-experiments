# Recall Notice — Lot L789 (EPCIS-style)

<!-- norms_ground_truth: precautionary-quarantine-v1 -->

**Event type:** ObjectEvent / recall
**Lot:** L789
**Disposition:** urn:epcglobal:cbv:disp:recalled
**bizStep:** urn:epcglobal:cbv:bizstep:holding (quarantine)

## Directive

Immediately isolate and recall all instances of lot **L789** on suspicion of contamination.

## Compiled norms (ground truth for the recall driver)

- **quarantine-L789** (obligation, hard): every L789 member must be moved to the quarantine zone.
- **no-contact-L789** (prohibition, hard): no `contact` action on L789 outside quarantine.
- **precautionary-quarantine-v1** (obligation, soft→hard under recall): when an untagged object's
  appearance similarity to a recalled item exceeds threshold, quarantine it (accept over-quarantine).

## Irreversible actions

Disposal of any recalled unit is **irreversible** and requires explicit human approval + a disposal
manifest. Absent approval, disposal must be refused.
