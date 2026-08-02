# Phase 10 conformance matrix

Date: 2026-07-30. Branch:
`codex/phase-10-scene-graph-drawing-intelligence`.

## Baseline before implementation

Environment: Windows, Python 3.13.13, .NET SDK 8.0.423, Node 24.18.0,
npm 11.16.0. Baseline commit:
`d1e84711841b4b262fc5563cb768904b8eefd811`, the fetched `origin/main`
containing PR #13.

| Suite | Command | Result |
|---|---|---:|
| Phase 9 conformance | `python scripts/test-phase9-conformance.py` | 90 passed |
| Root Python | `uv run pytest tests/ -q --basetemp=.pytest_cache\phase10-baseline-root` | 414 passed, 1 skipped |
| Gateway | `uv run --no-sync pytest -q --basetemp=.pytest_cache\phase10-baseline-gateway` after reinstalling local packages | 323 passed |
| Contracts | Gateway environment running `packages/contracts/tests` | 132 passed |
| Desktop Agent | `uv run --locked --group test --group ui-test pytest -q` | 156 passed |
| Managed Host Core | `dotnet test ... --configuration Release --no-restore` | 75 passed |
| Portal unit | `npm test` | 35 passed |
| Portal E2E | `npm run test:e2e` | 10 passed |
| Phase 8 conformance | `python scripts/test-phase8-conformance.py` | 39 Python + 23 Host passed |

The first root run completed all assertions but hit Windows temp cleanup
`WinError 5`; the recorded run uses repository-local `--basetemp`. Gateway and
contracts initially exposed stale installed local wheels; reinstalling
`autocad-contracts`, `autocad-cad-core`, `autocad-skill-catalog` and
`autocad-mcp` restored the source-consistent environment before the green
counts above.

## Accepted exact v0 support

| Source type | Required source evidence | Scene claim |
|---|---|---|
| LINE | finite WCS XY start/end | exact geometry and bounded line relations |
| CIRCLE | finite WCS XY center/radius, planar normal | exact geometry, concentric/containment/hole evidence |
| LWPOLYLINE | finite vertices+bulge, closed, elevation, planar normal | exact supported segments/contour |
| ARC | finite center/radius/start/end angles, planar normal | exact supported arc geometry |
| ELLIPSE/INSERT/TEXT/MTEXT/DIMENSION/SPLINE | Tier B only | unsupported or bounded projection with reason until separately proven |
| LT/File IPC | current summary read only | explicit scene capability missing; write remains off |

## Required relation/feature semantics

Relations required by v0 are connected endpoint, touch, intersect, overlap,
duplicate geometry, inside/contains, parallel, perpendicular, concentric and
aligned for documented supported type pairs. Exact source/derived evidence is
separate from bounded heuristics.

Features required by v0 are part/component, hole, repeated-hole pattern,
concentric group, bounded slot, centerline candidate and basic annotation link
only when source evidence exists. Every feature carries confidence, evidence,
algorithm version and limitations. Bbox-only hole/slot/part claims are
forbidden.

## Deterministic and adversarial plan

Golden fixtures cover rectangle lines, closed plate, four holes, flange,
obround slot, open chain, reversed duplicate, tolerance boundaries,
tangent/intersect circles, nested contours, rotation/translation,
large coordinates, mixed spaces, truncated and unsupported geometry.

Metamorphic checks bind shuffled entities/keys, reversed endpoints,
translation, rotation and engine-version change. Benchmarks cover 100, 1,000
and 5,000 simple entities, dense overlap, repeated grid and a near-cap
polyline. Gates assert candidate/cell/relation/byte counters; timing and peak
memory are retained observations.

## Final automated conformance

Deployed live runtime commit:
`165de0452af2b665deb67401b7c73420eefae226`. Final code/evidence commit:
`cdf591d05c9d89692b4cb8f283cd36cceeaf6b32`.

| Suite | Command | Result |
|---|---|---:|
| Phase 10 conformance and public security counters | `python scripts/test-phase10-conformance.py` | 63 passed; 2 tools, 7 resources, 0 destructive, 0 open-world |
| `cad_core` isolated | `uv run --locked --group test --project packages/cad_core pytest packages/cad_core/tests -q` | 22 passed |
| Gateway full | Gateway environment: `uv run --no-sync pytest -q` | 360 passed |
| Root Python | `uv run pytest tests/ -q --basetemp=.pytest_cache\phase10-final-root` | 420 passed, 1 skipped |
| Contracts full | Gateway environment running `packages/contracts/tests` | 144 passed |
| Desktop Agent full | `uv run --locked --group test --group ui-test pytest -q` | 160 passed |
| Managed Host Core | `dotnet test native/autocad_managed_host/tests/AutocadMcp.Host.Core.Tests/AutocadMcp.Host.Core.Tests.csproj --configuration Release` | 75 passed |
| Portal unit / build / E2E | `npm test`; `npm run build`; `npm run test:e2e` | 42 passed / build passed / 11 passed; npm audit 0 vulnerabilities |
| Phase 9 regression | `python scripts/test-phase9-conformance.py` | 94 passed |
| Phase 8 regression | `python scripts/test-phase8-conformance.py` | 39 Python + 23 Host passed |
| Migration/checksum negatives | selected Gateway migration/checksum tests | 2 passed, 23 deselected |
| LT default-off | direct Gateway-environment LT regression | 3 passed |
| Live evidence validator | `python scripts/validate-phase10-live-evidence.py` | passed |

The root skip is the documented Windows platform skip at
`tests/test_remote_policy.py:356`: symlink creation is unavailable in this
environment. It is pre-existing and does not disable a Phase 10 gate.

The first isolated `cad_core` attempt used the wrong Python environment. The
first contracts attempt used an environment without pytest. Both corrected
commands above passed; these were environment/retest failures, not code
failures. A first LT attempt used the root environment without FastMCP; the
correct Gateway environment passed all three default-off checks.

The deterministic dense/adversarial gates fail closed on candidate, spatial
cell, projected-byte, scene-byte and deadline limits. Local wall time and peak
memory are observations, not correctness gates.

## Live evidence result

The final public R25 matrix passed against three independently hashed fixtures:

| Drawing | Exact live result | No-effect proof |
|---|---|---|
| A | 6 nodes; 1 contour; 5 `hole` features; one four-hole `repeated_hole_pattern`; non-pattern circle excluded | revision, entity count and DWG hash unchanged |
| B | 8 nodes; bounded `slot` and `concentric_group`; near-slot and near-concentric negatives excluded | revision, entity count and DWG hash unchanged |
| C | 8 nodes; `degenerate_geometry`, `duplicate_geometry`, `open_contour`, `self_intersection`; valid geometry retained | revision, entity count and DWG hash unchanged |

The typed `drawing.cleanup-audit/1.1.0` workflow reused Drawing C scene
`scn_63aa70dee79e44b580a19612c0fd9e2e`, reported the same four issue codes,
completed, and retained `write_authority=false`.

The actual Gateway restart changed PID `173016` to `174089`. The standalone
Desktop Agent remained PID `69500`, reconnected with a new session, and the
public query returned the same scene/sections. The first reconnect attempt
exposed that systemd had also stopped cloudflared; restarting the tunnel and
verifying `/healthz=200` completed the retained retest without changing the
Agent process.

The owner/device/window-scoped DB comparison found no write event. Its
pre/post canonical write snapshot tables and digest were identical:
`sha256:ed0564b367c5cda86d340f5baf5bf1da5be328342467529649c2adee7194d2f6`.
The first DB collection used the external Auth0 subject, and a later command
split locale-formatted timestamps; the corrected durable owner and quoted
ISO-8601 retests passed.

Drawing C retains one explicit support limitation: the signed payload boundary
rejected the original `1e-7` tiny circle. The boundary stayed fail-closed and
the final exact zero-length LINE supplies the live degenerate case.

All retained artifacts and fixture hashes pass
`python scripts/validate-phase10-live-evidence.py` locally. GitHub Actions is
pending the final push of `cdf591d05c9d89692b4cb8f283cd36cceeaf6b32`; this document does not claim
hosted CI green yet.

**Phase 10 Engineering GO for the default-off bounded lab profile. Customer
Pilot NO-GO pending Phase 11 production hardening and pilot acceptance.**
