# Phase 8 implementation and acceptance evidence

> Status: **GO for the bounded Phase 8 signed R25 lab profile**.
> Delete/topology/LT-write packs remain disabled, production rollout is not
> implied, and mixed create-equivalent plus exact-transform plans fail closed.

## Accepted scope

- Branch: `docs/refresh-phase-8-after-phase-7`
- Phase 8 start commit: `b33b06af356c5296d35916ab5669eab31bc11d75`
- Acceptance date: 2026-07-28, Windows x64
- Runtime: AutoCAD Mechanical 2025, Managed .NET R25, Host `0.8.0`
- Test drawing reserved by the repository owner: `drawing33.dwg`
- Compiler: canonical CAD Program v1 compiler `1.1.0`
- Operation registry: `cad.operation-registry/1`
- Enabled live packs: copy/offset create-equivalent and move exact-transform
  for LINE, CIRCLE and LWPOLYLINE
- Disabled packs: delete, topology, trim, extend, fillet, chamfer, join,
  explode and LT write

The Host accepts only strict `cad.execution-plan/1` input with exact source,
compiler, effect, target-reference, validation, budget, runtime, package,
registry, policy and rollout pins. It does not execute source code, commands,
paths, URLs, assemblies or reflection supplied by the model.

## Automated regression

`pwsh -NoProfile -ExecutionPolicy Bypass -File
scripts/test-phase8-regression.ps1 -StopOnFailure` passed all 11 suites:

| Suite | Result |
|---|---:|
| Root Phase 0-5 | 409 passed, 1 skipped |
| Shared contracts | 113 passed |
| Host contract snapshots | 9 passed |
| Gateway | 250 passed |
| Desktop Agent | 142 passed |
| Managed Host Core | 72 passed |
| Web Portal unit/component | 35 passed |
| Web Portal browser E2E | 10 passed |
| Web Portal production build | succeeded |
| Phase 8 cross-stack conformance | 39 passed |
| Phase 8 Host checkpoint-v2 filter | 11 passed |

The separate aggregate command
`python scripts/test-phase8-conformance.py` also passed: 39 Python
cross-stack tests and 22 filtered Host contract tests.

The local R25 build command
`pwsh -NoProfile -ExecutionPolicy Bypass -File
scripts/build-phase8-r25-host.ps1` passed 72 Host Core tests and compiled the
R25 assembly against installed AutoCAD 2025 references.

## Signed R25 lab package

- Package ID: `autocad.managed_host.r25`
- Package version: `0.8.0`
- Package hash:
  `sha256:b30fa2d41698bc654c3bd350713ea244c64a0e26db4e876cdbbc30cdeb2ed236`
- Authenticode lab certificate:
  `A8D8F13F906B1A216EFE2A1EEFE89AFA9376A5F2`
- Operation registry hash:
  `sha256:1b840d43a4872322882f4443c07fb0f0b238cbb1d122cbefb4fe7e59097024a5`
- Host evidence digest:
  `sha256:323e9ae4b6bc2e1805873a5a27dbec2a88fe121ccd2ee7b0f113c492c08a8d8b`

This is a signed **lab-only** package without a production timestamp. The
signed installer verified the release manifest, artifact hashes and signer
before installing to the current-user Autodesk ApplicationPlugins directory.

## Live Mechanical 2025 acceptance

Runner:
`scripts/phase8-live-r25-e2e.py`

Retained evidence:
`docs/architecture/evidence/phase8-live-r25-e2e-20260728.json`

The runner reads the current-user Host bootstrap but never prints or persists
its session secret. It independently checks the installed signed package
manifest, derives the capability-manifest hash from the live Desktop manifest,
compiles exact materialized references from a live Model Space snapshot, then
executes preview, commit, recovery lookup, rollback preview, rollback commit
and rollback validation.

| Drill | Target | Commit checkpoint | Recovery result |
|---|---|---|---|
| `copy_entity` | LINE handle `373` | `cad.created-output.checkpoint/1` | Output erased; receipt/checkpoint found; rollback validation `true` |
| `move_entity` | CIRCLE handle `36A` | `cad.rollback.checkpoint/2` | Pre-image restored; receipt/checkpoint found; rollback validation `true` |

For both drills:

- preview aborted its database transaction and left the drawing unchanged;
- effect, receipt and checkpoint committed in one drawing transaction;
- recovery found the durable receipt and exact checkpoint;
- rollback preview reported zero conflicts and returned the deterministic
  rollback receipt ID;
- rollback commit and receipt were atomic;
- target fingerprint after rollback equaled the original fingerprint;
- allowlisted entity count before and after rollback was 37;
- rollback validation returned `valid: true`.

## Safety closure

- Mixed create-equivalent and exact-transform plans are rejected by Host
  admission until a compound atomic rollback contract exists. This prevents a
  plan with checkpoint v1 and v2 effects from receiving only a partial
  rollback.
- Create-equivalent rollback owns and erases only exact created outputs.
- Exact-transform rollback uses strict Host-generated pre-image descriptors
  and dependency closure, never generic AutoCAD Undo.
- Unsupported/custom entity types and missing capability evidence return
  `capability_missing`.
- The public MCP surface remains the Phase 7 tool/resource boundary; no
  primitive CAD write tool was added.
- Phase 7 receipt/recovery/rollback and `cad.program/0.2` regression suites
  remain green.

## Residual boundaries

This acceptance does not certify a production CA/timestamp, a public
OAuth/ChatGPT-to-Gateway run, LT write, mixed checkpoint strategies, or any
delete/topology operation. Those remain separate rollout gates and are not
claimed by Phase 8 core completion.
