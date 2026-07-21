# Phase 0 FastMCP facade compatibility evidence

Date: 2026-07-21  
Decision gate: **NO-GO pending the required CI matrix**

This report records the implementation and the evidence available on the
current Windows workstation. It does not claim that the six CI combinations
passed until GitHub Actions runs them.

## Scope delivered

Created the isolated project at `poc/fastmcp-phase0/` with:

- Pydantic contracts, in-memory services, and a FastMCP adapter in separate
  modules;
- exactly three tools and two resource templates;
- outer Starlette `/healthz` plus mounted `/mcp` and delegated FastMCP
  lifespan;
- stateless HTTP by default and a stateful compatibility path;
- RSA JWT verification through FastMCP `JWTVerifier` and
  `RemoteAuthProvider`;
- safe `not_found`, `backend_error`, and `internal_error` tool failures;
- structured output, `ResourceLink`, bounded PNG `ImageContent`, and static
  tool/resource snapshots;
- independent lockfile, dependency report, ADR, and CI workflow.

The root `pyproject.toml`, root `uv.lock`, legacy entrypoint, and
`src/autocad_mcp` were not changed.

## Baseline

Captured before POC implementation:

- commit: `7d6a78283e5e50d8b89c38d551691abcd4f4f074`;
- Python: `3.13.13`;
- uv: `0.9.26`;
- root `uv sync --locked`: passed;
- root `uv run pytest tests/ -q`: `302 passed, 1 skipped, 9 warnings`;
- root warnings are the existing `ezdxf`/`pyparsing` deprecations;
- root `pyproject.toml` and `uv.lock` hashes are recorded in the dependency
  report.

Pre-existing dirty paths were preserved: three phase docs, the Cloudflare
tunnel script, `.codex/`, and the existing architecture documents.

## POC test evidence

Command:

```powershell
cd poc/fastmcp-phase0
uv sync --locked --group test
uv run --locked --group test pytest -q
```

Result on Windows/Python 3.13.13: **14 passed**.

The passing tests cover:

| Area | Evidence |
| --- | --- |
| Component contract | `tools/list` snapshot has exactly three tools; resource templates snapshot has exactly two templates. |
| In-memory protocol | FastMCP `Client` performs initialize, list, all three calls, resource reads, structured output, `ResourceLink`, `ImageContent`, and PNG artifact read. |
| HTTP stateful/stateless | MCP SDK `ClientSession` plus Streamable HTTP performs initialize, `tools/list`, and `tools/call` in both modes. |
| Isolation | Concurrent requests receive different correlation IDs; authenticated calls pass JWT `sub` into the service. |
| Auth | Valid RSA JWT succeeds; missing scope is rejected before a service call; missing token, signature, issuer, audience, and expiry variants return `401`. |
| Metadata | Protected-resource metadata advertises the resource URL, authorization server, and `autocad.read`. |
| Request guards | Invalid Host and Origin return `403`; missing Origin is accepted; `X-Forwarded-Host` is not trusted by the outer guard. |
| Error safety | Domain failures become `isError=true` with safe codes; unexpected details and paths are masked; invalid input does not call the service. |
| API boundary | Static check finds no FastMCP private manager/module import. |

`git diff --check` passed after the POC implementation.

## Schema and dependency evidence

- Schema snapshots: `poc/fastmcp-phase0/snapshots/tools.json` and
  `poc/fastmcp-phase0/snapshots/resources.json`.
- Dependency tree and hashes: `docs/architecture/phase0-fastmcp-dependency-report.md`.
- Local resolved POC versions include FastMCP `3.4.4`, MCP `1.28.1`, and
  Starlette `1.3.1`; the root lock remains on MCP `1.26.0` and Starlette
  `0.52.1`.
- OpenAI Apps SDK guidance requires accurate read-only/destructive/open-world
  hints and treats structured content/content metadata as user-visible. The
  POC therefore tests annotations and keeps tokens out of outputs.

## Required CI matrix

Workflow: `.github/workflows/phase0-fastmcp.yml`.

| OS | Python 3.10 | Python 3.12 | Python 3.13 |
| --- | --- | --- | --- |
| `ubuntu-latest` | not run locally | not run locally | not run locally |
| `windows-latest` | not run locally | not run locally | local equivalent passed |

The workflow independently locks/syncs the POC, checks exact FastMCP
version, runs tests, and verifies snapshots do not change. The Phase 0 gate
remains `NO-GO pending CI` until all six jobs pass.

## Deliberate limits

Not tested in Phase 0: Auth0 DCR or ChatGPT login, real ChatGPT Web, real
AutoCAD/DWG, multi-user ownership, SQLite, WebSocket, Desktop Agent, write
operations, reverse-proxy production behavior, and a real Linux runner.

## Conclusion

The local spike is technically green and shows that FastMCP 3.4.4 can carry
the proposed three-tool facade without touching the legacy server. The
architecture decision is therefore suitable for CI review, but the formal
Phase 0 `GO` gate is intentionally not claimed until the independent
Windows/Linux and Python 3.10/3.12/3.13 matrix passes and this evidence is
approved.
