# ADR 0001 — FastMCP 3 Gateway facade boundary

- Status: accepted; regression revalidated after Phase 1–3
- Original decision date: 2026-07-21
- Current review date: 2026-07-22
- Scope: Gateway MCP interface only

## Context

At the Phase 0 decision point, the legacy server used the FastMCP implementation
bundled in the root MCP SDK dependency. The proposed multi-user Gateway needed a
separate compatibility check against PrefectHQ FastMCP `3.4.4`, whose dependency
graph included a newer Starlette line. The compatibility spike was therefore
isolated under `poc/fastmcp-phase0/` with its own project and lockfile.

Phase 1, Phase 2, and Phase 3 have since been merged into `main`. They introduced
the shared CAD application service, the production Gateway facade, durable
SQLite/WebSocket behavior, and the simulated Agent. This ADR now distinguishes
the historical Phase 0 decision from the current regression status; it does not
claim that root or legacy files remained unchanged after later phases.

## Decision

1. FastMCP belongs only to the Gateway MCP interface. Domain contracts and
   application services do not import FastMCP, MCP transport, or Starlette
   types.
2. Phase 0 and the production Gateway pin `fastmcp==3.4.4` in their isolated
   projects and lockfiles.
3. An outer Starlette application owns `/healthz`, `/mcp` mounting, and the
   FastMCP lifespan. The MCP app is mounted below that outer application.
4. Stateless Streamable HTTP is the default. Stateful HTTP remains a
   compatibility test because clients may use it.
5. JWT verification performs signature, issuer, audience, expiry, token-format,
   and required `sub` validation. Per-tool and per-resource authorization uses
   FastMCP component checks such as `require_scopes("autocad.read")`.
6. The user identity is the JWT `sub` claim. `client_id` and `azp` are never
   accepted as identity fallbacks.
7. The Phase 0 facade exposes only `cad_list_devices`, `cad_observe`, and
   `cad_get_job`, plus two bounded read-only resource templates.
8. A small outer host/origin guard normalizes rejected requests to the Phase 0
   contract's `403` response and does not trust `X-Forwarded-Host`. FastMCP's
   own guard remains enabled as a second boundary.
9. Phase 0 observation evidence must be derived from the real headless
   `EzdxfBackend` through `CadApplicationService`; snapshots may not reconstruct
   hard-coded entity counts.
10. Public tool signatures and Pydantic contracts share constrained annotated
    types, and FastMCP strict input validation is enabled to reject unsafe type
    coercion before service invocation.

## Consequences

- Phase 0 remains an isolated compatibility and regression project; it does not
  migrate the legacy server or add production features.
- The production public contracts from Phase 2–3 are not changed by this ADR
  refresh.
- Authentication failures and authorization failures have different evidence:
  invalid JWTs fail the HTTP authentication boundary, while valid tokens that
  lack scope are filtered and denied by FastMCP component authorization.
- Snapshot summaries are materialized only after both `drawing.info` and
  `entity.list` succeed and agree on the real entity count.
- Preview artifacts are bounded, owner-scoped, MIME-checked, and PNG-signature
  checked before they are returned.
- CI owns the Windows/Linux and Python 3.10/3.12/3.13 proof plus root, Gateway,
  contracts, and simulated-Agent regression gates.

## References

- [FastMCP installation and versioning](https://gofastmcp.com/getting-started/installation)
- [FastMCP HTTP deployment](https://gofastmcp.com/deployment/http)
- [FastMCP authorization](https://gofastmcp.com/servers/authorization)
- [FastMCP testing](https://gofastmcp.com/servers/testing)
- [OpenAI Apps SDK MCP server guidance](https://developers.openai.com/apps-sdk/build/mcp-server)
