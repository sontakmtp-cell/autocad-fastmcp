# Phase 6 Portal failure matrix

> Evidence date: 2026-07-27.
>
> Scope: Portal unit/component tests and Playwright with a bounded mock Gateway.
> This is not live AutoCAD Mechanical 2025 evidence and does not prove release
> signing, revoke/re-pair, telemetry soak, or production readiness.

| Scenario | Expected Portal behavior | Evidence | Status |
|---|---|---|---|
| Phase 6 UI flag absent | Do not fetch or render CAD Program records | `phase6-env.test.ts` | Pass |
| Managed Write UI flag absent | Show write off | `phase6-env.test.ts`, `Phase6Status.test.tsx` | Pass |
| Kill switch active | Managed Write UI remains off even if other flags are on | `phase6-env.test.ts` | Pass |
| Exact runtime/package/capability binding | Render all pins and hashes as owner-safe summary | component test + Playwright program/preview flow | Pass |
| Preview succeeds | Say transaction aborted and DWG unchanged; never say approved | Playwright preview assertion | Pass |
| Preview runtime changes | Show invalidation and request a new preview | Playwright `preview-a-stale` | Pass |
| Capability missing | Render an explicit capability warning | component copy + program rendering path | Pass |
| Commit outcome unknown | Show no blind-retry action | component test + Playwright job assertion | Pass |
| Cross-owner resource guess | Return not found without leaking document data | Playwright owner B direct URL | Pass with mock Gateway |
| Gateway malformed resource | Reject at the server-side Zod boundary | contract unit test | Pass |
| AutoCAD LT write | No Portal mutation/action exists; runtime enforcement remains outside Portal | static safety validator | Pass for Portal surface only |
| Live preview abort on Mechanical 2025 | Entity/revision unchanged | No live run in this scope | Pending |
| Live commit exactly once | Durable receipt and unchanged count on duplicate | No live run in this scope | Pending |
| Live revoke/re-pair | Active WSS closes and reconnect is blocked | No evidence supplied in this scope | Pending |
| CA signing and trusted timestamp | Production package verifies | No evidence supplied in this scope | Pending |
| Telemetry soak 3–7 days | No release-blocking reliability signal | Soak not completed in this scope | Pending |

## Release decision

- Portal engineering checks: GO for local integration.
- Phase 6 Engineering GO: not decided by this Portal-only matrix; live Managed
  Host/Gateway/Agent evidence is still required.
- Customer Pilot GO: **NO-GO**. CA signing/timestamp, live revoke/re-pair and
  telemetry soak are not proven here.
