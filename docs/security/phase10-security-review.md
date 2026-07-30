# Phase 10 security diff review

## Status and scope

Status on 2026-07-30: **integrated review passed; Engineering NO-GO on live
acceptance**.

Reviewed surfaces are the strict scene contracts, pure scene engine, bounded
runtime projection, immutable owner-scoped Gateway repository/service, signed
cursor, two-tool/seven-resource public surface and read-only Portal projection.
The accepted trust boundary is
[`phase10-threat-model.md`](phase10-threat-model.md).

This document is not itself live AutoCAD evidence. Integrated workflow changes
were reviewed; the required three signed-lab R25 drawings and real Gateway
process restart remain incomplete.

## Automated evidence

`python scripts/test-phase10-conformance.py` passed **41 tests** and checked
these deterministic public counters:

- exactly two scene tools;
- exactly seven bounded scene resources;
- zero destructive scene tools;
- zero open-world scene tools.

The suite covers strict/finite contracts, canonical identity/digests,
entity-order determinism, bounded spatial candidates, dense overlap failure,
owner isolation, immutable restart retrieval, cursor tamper/binding, prompt-text
redaction, default-off flag dependencies and R25/ezdxf/LT projection honesty.
Wall-clock observations are retained for diagnostics but are not pass/fail
criteria.

Regression checks passed independently:

- Gateway full: **341 passed**;
- root Python: **416 passed, 1 skipped**;
- contracts full: **143 passed**;
- Desktop Agent full: **159 passed**;
- Phase 9 conformance: **94 passed**;
- Phase 8 Python conformance: **39 passed**;
- Managed Host Core: **75 passed**;
- Portal unit: **42 passed** and Portal E2E: **11 passed** in the Portal slice.

## Review result

No reportable Critical or High security finding was identified in the
integrated default-off surfaces. This does not waive the remaining live
acceptance gates.

| Risk | Reviewed control | Current result |
| --- | --- | --- |
| Owner/snapshot/resource IDOR | Owner-filtered repository/service and safe `not_found`; Portal bearer only | Headless covered |
| Cursor forgery/replay | HMAC cursor binds owner, scene, section, filter, offset and projection | Headless covered |
| Digest/order manipulation | Strict finite values, canonical ordering and domain-separated digests | Headless covered |
| Duplicate/conflicting scene | Immutable insert, exact idempotency and conflict rejection | Headless covered |
| Dense geometry DoS | Entity/byte/cell/candidate/relation/scene caps and no all-pairs fallback | Deterministic counter coverage |
| Prompt injection/data exfiltration | Prompt-like drawing text omitted/redacted; no URL/path/command sink | Headless covered |
| Query/code injection | Closed typed filters; no expression, regex, SQL, path or plugin editor | Headless and Portal covered |
| Inference as write authority | Issue `write_authority=false`; scene IDs/features do not enter commit authority | Integrated workflow covered |
| Approval/risk weakening | Existing Phase 7–9 authority remains unchanged | Regressions green; live matrix incomplete |
| Silent runtime downgrade | Explicit runtime/source capability evidence; LT claims no Phase 10 geometry/write | Headless cross-runtime covered |
| Default-on exposure | All Phase 10 flags default off with dependency validation | Headless covered |

## Residual gates

- Run Drawing A, B and C on AutoCAD Mechanical 2025 R25 with the signed bounded
  lab profile.
- Prove exact document revision is unchanged before/after every scene build and
  query.
- Prove durable scene retrieval after a real Gateway process restart with an
  independent no-CAD-effect comparison.
- Retain failures/retests, hashes, operator/date and capability evidence.

Until those gates pass, do not claim Phase 10 Engineering GO or Customer Pilot
readiness.
