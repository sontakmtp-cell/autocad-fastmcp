# ADR 0001 — FastMCP 3 Gateway facade boundary

- Status: proposed after Phase 0 spike; Phase 1 is still gated
- Date: 2026-07-21
- Scope: Gateway MCP interface only

## Context

The legacy server imports the FastMCP implementation bundled in
`mcp==1.26.0`. The proposed multi-user Gateway needs a separate compatibility
check against PrefectHQ FastMCP `3.4.4`, whose dependency graph includes a
newer Starlette line. The legacy entrypoint and root lockfile must therefore
remain untouched.

## Decision

1. FastMCP belongs only to the Gateway MCP interface. Domain contracts and
   application services do not import FastMCP types or decorators.
2. The Gateway facade pins `fastmcp==3.4.4` in its own project and lockfile.
3. An outer Starlette application owns `/healthz`, `/mcp` mounting, and the
   FastMCP lifespan. The MCP app is mounted below that outer application.
4. Stateless Streamable HTTP is the default for the future Gateway. Stateful
   HTTP remains a compatibility test because clients may use it.
5. Authentication is resource-server configuration at the MCP boundary. The
   service receives a principal created from the JWT `sub` claim and scopes;
   it does not use `client_id` or `azp` as the user identity.
6. The Phase 0 facade exposes only `cad_list_devices`, `cad_observe`, and
   `cad_get_job`, plus the two bounded resource templates.
7. A small outer host/origin guard normalizes rejected requests to the Phase 0
   contract's `403` response and does not read `X-Forwarded-Host`. FastMCP's
   own guard remains enabled as a second boundary.
8. Phase 1 may start only after the Phase 0 evidence report is reviewed and
   the six OS/Python CI combinations pass.

## Consequences

- The legacy `pyproject.toml`, `uv.lock`, entrypoint, and `src/autocad_mcp`
  package do not need migration changes for this spike.
- Schema and annotation compatibility can be reviewed independently of
  production ownership, WebSocket, persistence, or AutoCAD write semantics.
- The POC has a separate dependency graph. On the local Windows/Python 3.13
  run it resolves FastMCP 3.4.4, MCP 1.28.1, and Starlette 1.3.1, while the
  legacy lock remains on MCP 1.26.0 and Starlette 0.52.1.
- Real Auth0, ChatGPT login, reverse-proxy behavior, Linux, Python 3.10,
  Python 3.12, and Python 3.13 CI runs remain outside local evidence.

## References

- [FastMCP installation and versioning](https://gofastmcp.com/getting-started/installation)
- [FastMCP HTTP deployment](https://gofastmcp.com/deployment/http)
- [FastMCP authorization](https://gofastmcp.com/servers/authorization)
- [FastMCP testing](https://gofastmcp.com/servers/testing)
- [OpenAI Apps SDK MCP server guidance](https://developers.openai.com/apps-sdk/build/mcp-server)
