# Phase 10 conformance matrix

Date: 2026-07-30. Branch:
`codex/phase-10-scene-graph-drawing-intelligence`.

## Baseline before implementation

Environment: Windows, Python 3.13.13, .NET SDK 8.0.423, Node 24.18.0,
npm 11.16.0. Baseline commit:
`48365bccccb9c1214a4447072410f99ac8361dc3` (latest local `main`, containing
PR #13 plus the local `AGENTS.md` instruction commit). The corresponding
fetched `origin/main` was `d1e84711841b4b262fc5563cb768904b8eefd811`.

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

Final product-code head used for automated gates:
`7a4931da94d46c8f4a3227062796d57d04c51c6a`. The retained live evidence was
regenerated at `7e00fdc366a9b0a90b27a27a96f94f9e190ef00a` after clarifying its
repository-reopen semantics and retention limitations.

| Suite | Result |
|---|---:|
| Phase 10 conformance and public security counters | 41 passed; 2 tools, 7 resources, 0 destructive, 0 open-world |
| `cad_core` | 12 passed |
| Gateway full | 341 passed |
| Root Python | 416 passed, 1 skipped |
| Contracts full | 143 passed |
| Desktop Agent full | 159 passed |
| Managed Host Core | 75 passed |
| Portal unit / build / E2E | 42 passed / build passed / 11 passed |
| Phase 9 regression | 94 passed |
| Phase 8 Python regression | 39 passed |

The deterministic dense/adversarial gates fail closed on candidate, spatial
cell, projected-byte, scene-byte and deadline limits. Local wall time and peak
memory are observations, not correctness gates.

## Live evidence result

Engineering GO requires three identified R25 drawings:

- A: plate with four holes and repeated pattern;
- B: exact slot plus concentric geometry;
- C: duplicate/degenerate/open-contour cleanup.

For each, retain fixture identity/hash, package/Agent/Host/runtime capabilities,
source snapshot/revision, scene/profile/engine digests, counts, commands,
failures/retests, operator/date and before/after proof that document revision
did not change. Restart Gateway and retrieve the same scene/report without a
new CAD effect. Headless and ezdxf evidence cannot replace live R25.

The signed bounded R25 lab run retained
`evidence/phase10-live-r25-drawing33-20260730.json`. It proves 41 exact source
entities, unchanged document revision, no requested write and durable retrieval
of the same scene after reopening the repository database/service.

`drawing33.dwg` produced 265 relations, 7 contours, 11 read-only features and
11 issues. It did not produce the required hole/repeated-hole, slot or
concentric feature evidence. The repository contains no separately identified
Drawings A, B and C; the combined drawing also lacks the complete required
expected outcomes, including the degenerate cleanup case.

Therefore automated/headless/security gates are green, but the required
three-drawing live acceptance gate and real Gateway process restart are
incomplete. **Phase 10 Engineering NO-GO; Customer Pilot NO-GO.**
