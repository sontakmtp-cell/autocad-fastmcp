# Phase 10 live R25 evidence — partial

Status on 2026-07-30: **one combined drawing executed; Engineering NO-GO**.

Retained machine-readable result:
`phase10-live-r25-drawing33-20260730.json`.

## Common evidence

Record the final baseline and implementation commits, operator/date, Windows and
AutoCAD Mechanical 2025 R25 versions, device/Agent/Host/package identities and
hashes, capability manifest, policy/profile/engine versions, exact commands,
failures/retests and retained log/artifact paths.

For each drawing retain:

- drawing fixture identity and pre-run file hash;
- source snapshot ID and exact document revision;
- scene ID/source digest/scene digest;
- counts, capabilities, warnings and completeness;
- pre/post document revision and proof they are identical;
- Gateway restart/retrieval result;
- proof of zero CAD effect.

## Executed combined fixture — `drawing33.dwg`

The signed lab package `autocad.managed_host.r25` version `0.10.3` projected
41/41 entities exactly from AutoCAD Mechanical 2025 R25. The source included
18 LINE, 12 CIRCLE, 7 LWPOLYLINE and 4 ARC entities.

Proven:

- document revision unchanged before/after;
- the on-disk DWG SHA-256 unchanged before/after;
- no CAD write requested;
- 265 relations, 7 contours, 20 components, 11 features and 11 issues;
- the same immutable scene and 11 issues retrieved after repository database
  close/reopen in the evidence process;
- signed, bounded, lab-only package identity and hashes retained.

Not proven by this fixture:

- hole and repeated-hole pattern;
- slot;
- concentric group;
- degenerate geometry cleanup.

The observed feature types were `part` and `centerline_candidate`. The observed
issues were ten `duplicate_geometry` and one `open_contour`.

## Failures and retests retained

- The first Host run advertised ARC but used a legacy read fingerprint that
  rejected ARC as `capability_missing`; the signed lab bundle was rebuilt.
- The next ARC projection used ambiguous angle field names; bundle `0.10.3`
  emits explicit radians and the live projection passed.
- Gateway public projection initially referenced obsolete ARC attributes; fixed
  by commit `97b7020`.
- The full public scene digest initially exceeded the bounded contract payload;
  commit `7a4931d` binds bounded per-section hashes and the live service build
  passed.

The exact final capture command is retained in the JSON. The run used the
direct Managed .NET read port; no standalone Desktop Agent process identity is
claimed.

## Required live rows still missing

- Drawing A: identified plate/four-hole fixture with four hole features and a
  repeated-hole pattern.
- Drawing B: identified exact slot and concentric fixture.
- Drawing C: identified duplicate/degenerate/open-contour cleanup fixture with
  the typed durable cleanup workflow report.
- A real Gateway process restart/reconnect followed by public scene/report
  retrieval and an independent no-effect comparison.

## Decision

The executed drawing is valid partial live evidence, but it cannot substitute
for all three required drawings and expected outcomes. **Engineering NO-GO and
Customer Pilot NO-GO.**
