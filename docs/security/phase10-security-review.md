# Phase 10 security diff review

## Status and scope

Status on 2026-07-30: **integrated review and bounded live acceptance passed;
Engineering GO for the default-off lab profile**.

Reviewed surfaces are the strict scene contracts, pure scene engine, bounded
runtime projection, immutable owner-scoped Gateway repository/service, signed
cursor, two-tool/seven-resource public surface and read-only Portal projection.
The accepted trust boundary is
[`phase10-threat-model.md`](phase10-threat-model.md).

This document is not itself live AutoCAD evidence. The retained A/B/C, cleanup
workflow, Gateway restart and scoped no-effect DB artifacts supply that
evidence at deployed runtime commit
`165de0452af2b665deb67401b7c73420eefae226`; final code/evidence commit:
`FINAL_EVIDENCE_COMMIT`.

## Automated evidence

`python scripts/test-phase10-conformance.py` passed **63 tests** and checked
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

- Gateway full: **360 passed**;
- root Python: **420 passed, 1 skipped**;
- contracts full: **144 passed**;
- `cad_core` isolated: **22 passed**;
- Desktop Agent full: **160 passed**;
- Phase 9 conformance: **94 passed**;
- Phase 8 conformance: **39 Python + 23 Host passed**;
- Managed Host Core: **75 passed**;
- Portal unit: **42 passed**, build passed, Portal E2E: **11 passed**, npm audit
  reported **0 vulnerabilities**;
- selected migration/checksum negatives: **2 passed, 23 deselected**;
- direct Gateway-environment LT default-off regression: **3 passed**.

The root skip is the documented pre-existing Windows symlink skip at
`tests/test_remote_policy.py:356`, not a disabled Phase 10 gate. Initial
`cad_core` and contracts commands used the wrong Python environments; corrected
isolated/repo environments passed, so those are retained environment retests,
not security or code failures. The first direct LT attempt used the root
environment without FastMCP; the corrected Gateway environment passed.

## Review result

No reportable Critical or High security finding was identified in the
integrated default-off surfaces. The final live matrix also passed the
read-only, provenance and no-effect gates.

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
| Approval/risk weakening | Existing Phase 7–9 authority remains unchanged | Regressions and live cleanup workflow green |
| Silent runtime downgrade | Explicit runtime/source capability evidence; LT claims no Phase 10 geometry/write | Headless and managed R25 evidence covered |
| Default-on exposure | All Phase 10 flags default off with dependency validation | Headless covered |
| Live read path causing CAD effect | Per-drawing revision/entity/hash checks plus owner/device DB snapshot comparison | Three drawings, cleanup, restart and no-effect DB passed |

## Live review result

- Drawing A produced five hole features and one four-hole pattern while
  excluding the non-pattern circle.
- Drawing B produced the bounded slot and concentric group while excluding both
  tolerance-negative controls.
- Drawing C reported four required issue codes, retained exact valid geometry,
  and the typed cleanup workflow reused the same scene with
  `write_authority=false`.
- Gateway PID changed `173016` to `174089`; unchanged Agent PID `69500`
  reconnected under a new session and retrieved the same scene.
- Scoped DB evidence found no write event and identical canonical pre/post
  write snapshot digest
  `sha256:ed0564b367c5cda86d340f5baf5bf1da5be328342467529649c2adee7194d2f6`.
- `python scripts/validate-phase10-live-evidence.py` passed locally.

The retained failures/retests cover the fail-closed tiny-circle rejection,
privacy-safe valid-geometry gate, Gateway-owned scene capability correction,
deployed venv synchronization, cloudflared restart, and corrected DB
owner/timestamp arguments. Drawing C explicitly limits the degenerate live
claim to the exact zero-length LINE; it does not claim the rejected tiny
circle.

GitHub Actions remains pending the final push and this review does not claim CI
green yet. **Phase 10 Engineering GO applies only to the default-off bounded
lab profile. Customer Pilot remains NO-GO pending Phase 11.**
