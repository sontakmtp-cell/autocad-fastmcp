# Phase 0 FastMCP dependency report

Original report date: 2026-07-21  
Current regression review: 2026-07-22

## Isolation

The POC uses `poc/fastmcp-phase0/pyproject.toml` and its own
`poc/fastmcp-phase0/uv.lock`. It consumes the root package and the shared
`packages/cad_core` package as local, non-editable path dependencies. FastMCP is
not a root dependency and is not imported by CAD Core, shared contracts, or the
Gateway domain package.

Phase 1–3 have changed the root project since the historical Phase 0 baseline.
That later work does not merge the POC lockfile into the root lockfile and does
not move FastMCP into the domain/core layer.

## Direct dependencies

| Dependency | Constraint |
| --- | --- |
| `fastmcp` | `==3.4.4` |
| `autocad-mcp` | local path `../..` |
| `cad-core` | local path `../../packages/cad_core` |
| `httpx` | `>=0.27,<1.0` |
| `PyJWT[crypto]` | `>=2.11.0,<3.0` |
| `asgi-lifespan` | `>=2.1,<3.0` |
| test group | `pytest`, `pytest-asyncio` |

## Locked Phase 0 versions

Command: `uv tree --locked` from `poc/fastmcp-phase0/` and exact-version checks
in `.github/workflows/phase0-fastmcp.yml`.

- FastMCP `3.4.4`
- MCP SDK `1.28.1`
- Starlette `1.3.1`
- Pydantic `2.13.4`
- HTTPX `0.28.1`
- PyJWT `2.13.0`
- Authlib `1.7.2`
- Ezdxf `1.4.4`
- Pillow `12.3.0`

The compatibility matrix verifies the same isolated lock on Ubuntu and Windows
with Python 3.10, 3.12, and 3.13. The workflow also runs `uv lock --check` for
the root, Phase 0, Gateway, and simulated-Agent projects.

## Current repository identity

The following are Git object identities on the Phase 0 hardening branch. They
are recorded separately from the historical SHA-256 values below because Git
blob SHA and file-content SHA-256 are different hash schemes.

- root `pyproject.toml` blob: `d433044351d082863cc62b778a1ec27b512bfefc`
- root `uv.lock` blob: `7490094c31dbab0b14978e4c9b7b1203fbccad80`
- POC `pyproject.toml` blob: `eeabbfb7e7859913f62054dab6a160a6e7f66e45`
- POC `uv.lock` blob: `869fa5b931c79fdcff75276987602fdeaaab15f8`

No dependency or lockfile change was required for the hardening patch. CI checks
that resolving with `--locked` succeeds and that `uv lock --check` reports no
drift.

## Historical Phase 0 hashes

These values are retained as historical evidence from the original Phase 0
report. They must not be interpreted as the current root state after Phase 1–3.

Captured before the original POC edits:

- root `pyproject.toml`: `E22346C7B41544BB88C14A6B66E98E460EAA51C81F7ABF61AF9B04A59E76034E`
- root `uv.lock`: `13E3871B41B59187881E5ABE27262BCE333AAB8CBD097EEADBB63622394A9974`

Original POC files:

- POC `pyproject.toml`: `6DE3C5F3EAE4CE0E49845C9F2ADE64F02BEC71632351F63B0F1A55245796AE6D`
- POC `uv.lock`: `E37AC2B30F2987748C7B2E5975121B51605CE53E20C1DB98D1897CA49789A2A8`
