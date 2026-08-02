# Phase 10 live R25 evidence — final bounded lab result

Status on 2026-07-30: **live acceptance PASS; Engineering GO for the
default-off bounded lab profile**.

Baseline commit:
`d1e84711841b4b262fc5563cb768904b8eefd811`. Deployed live runtime commit:
`165de0452af2b665deb67401b7c73420eefae226`. Final code/evidence commit:
`cdf591d05c9d89692b4cb8f283cd36cceeaf6b32`.

The earlier combined `drawing33.dwg` run remains historical partial evidence.
The acceptance decision below is based on three independently hashed fixtures,
the typed cleanup workflow, an actual Gateway restart/reconnect and a scoped
durable DB no-effect comparison.

## Retained machine-readable evidence

- `phase10-live-r25-drawing-a-20260730.json`;
- `phase10-live-r25-drawing-b-20260730.json`;
- `phase10-live-r25-drawing-c-20260730.json`;
- `phase10-live-cleanup-workflow-20260730.json`;
- `phase10-live-gateway-restart-20260730.json`;
- `phase10-live-no-effect-db-20260730.json`;
- `phase10-headless-conformance-20260730.json`.

`python scripts/validate-phase10-live-evidence.py` passes the fixture
manifest/hash, schema, commit, scene/digest, cleanup, restart and no-effect
bindings locally.

## Drawing A — plate and holes

- Fixture: `phase10-drawing-a-r25/1`;
- DWG SHA-256:
  `e7d5891cd8d88fd34a7b1f91d2e092c4d068c7cae7cf902ca526eacfe254d257`;
- document revision: `4373173421687966`;
- 6 nodes, 10 relations, 1 contour, 7 features, 0 issues;
- 5 `hole` features and one four-hole `repeated_hole_pattern`;
- the fifth non-pattern circle was correctly excluded from the pattern;
- revision, entity count and DWG hash unchanged; no write requested or CAD
  effect attempted.

## Drawing B — slot and concentric geometry

- Fixture: `phase10-drawing-b-r25/1`;
- DWG SHA-256:
  `47baa712c04d5cd20255b30be5bd12f2b1a23e03e3968848df4ebc61f18115bd`;
- document revision: `7542304900287789`;
- 8 nodes, 12 relations, 1 contour, 3 features, 1 read-only issue;
- bounded `slot` and `concentric_group` detected;
- near-slot and near-concentric tolerance negatives excluded;
- revision, entity count and DWG hash unchanged; no write requested or CAD
  effect attempted.

## Drawing C — cleanup and anomaly fixture

- Fixture: `phase10-drawing-c-r25/1`;
- DWG SHA-256:
  `6fc0673129d73315b4edb885cf0214401ccbd04f7451dad468cabccf43b379dd`;
- document revision: `6342267835475121`;
- 8 nodes, 44 relations, 2 contours, 3 features, 5 issue records;
- required distinct issue codes:
  `degenerate_geometry`, `duplicate_geometry`, `open_contour`,
  `self_intersection`;
- exact valid geometry was not flagged for cleanup;
- revision, entity count and DWG hash unchanged; no write requested or CAD
  effect attempted.

The original `1e-7` tiny-circle fixture was rejected fail-closed by the signed
payload boundary. The final live degenerate case is an exact zero-length LINE;
the tiny circle is not claimed. A first valid-geometry assertion depended on a
privacy-hashed raw layer name; the retest bound exact geometry identity instead
and passed without weakening privacy.

## Typed cleanup workflow

`drawing.cleanup-audit/1.1.0` completed as run
`wfr:8eeb886319ba41b79606aa7d67acdf12`. It reused Drawing C scene
`scn_63aa70dee79e44b580a19612c0fd9e2e`, the same source/scene digests and exact
snapshot/revision. Its report returned the same four issue codes with
`write_authority=false`; no write tool was invoked.

The first live start exposed that Gateway analysis capability `scene.core/1`
was being evaluated as an Agent capability. The correction contributes that
capability only when the real Gateway scene port is enabled and leaves Agent
write checks unchanged. A first deployment retest still loaded copied editable
paths; synchronizing the release venv to the exact deployed source and
verifying the loaded module path produced the final PASS.

## Actual Gateway restart and no-effect proof

Gateway PID changed from `173016` to `174089`. Standalone Desktop Agent PID
`69500` remained unchanged and reconnected from session
`session-5125c353-5868-4a2e-8d54-da497c6e3d66` to
`session-1ea445bf-a60a-4ee2-86f0-3b324043db82`. The public query returned the
same Drawing C scene and every retained section after restart.

The Gateway stop/start also stopped its dependent cloudflared service. The
first reconnect therefore returned Cloudflare 1033. Restarting
`autocad-mcp-cloudflared.service`, verifying public `/healthz=200`, and keeping
the same Agent PID produced the retained successful reconnect/retest.

The owner/device/window-scoped DB evidence found zero write events. Its
canonical pre/post write tables and SHA-256 were byte-for-byte identical:

```text
sha256:ed0564b367c5cda86d340f5baf5bf1da5be328342467529649c2adee7194d2f6
```

The first DB collection used the external Auth0 subject rather than the durable
owner binding. Another command passed locale-formatted timestamps without
quoting. Correct durable owner and quoted ISO-8601 retests passed every DB,
session, scene and no-write gate.

## Local automated evidence

| Suite | Result |
|---|---:|
| Phase 10 conformance | 63 passed |
| `cad_core` isolated | 22 passed |
| Gateway full | 360 passed |
| Root Python | 420 passed, 1 skipped |
| Contracts full | 144 passed |
| Desktop Agent full | 160 passed |
| Managed Host Core | 75 passed |
| Portal unit / build / E2E | 42 passed / build passed / 11 passed; npm audit 0 vulnerabilities |
| Phase 9 regression | 94 passed |
| Phase 8 regression | 39 Python + 23 Host passed |
| Migration/checksum negatives | 2 passed, 23 deselected |
| LT default-off | 3 passed |

The root skip is the pre-existing Windows platform skip at
`tests/test_remote_policy.py:356`: symlink creation is unavailable. Initial
isolated `cad_core` and contracts runs used incorrect Python environments; the
corrected commands passed and these are retained environment/retest failures,
not code failures. The first LT attempt used the root environment without
FastMCP; the corrected Gateway-environment run passed.

GitHub Actions has not yet run against `cdf591d05c9d89692b4cb8f283cd36cceeaf6b32`; hosted CI green
is not claimed.

## Decision

All mandatory bounded Phase 10 live, validator, no-effect and local regression
gates pass. **Phase 10 Engineering GO applies to the default-off bounded lab
profile. Customer Pilot remains NO-GO pending Phase 11 production hardening and
pilot acceptance.**
