# Phase 0 FastMCP dependency report

Date: 2026-07-21

## Isolation

The POC uses `poc/fastmcp-phase0/pyproject.toml` and its own
`poc/fastmcp-phase0/uv.lock`. It consumes the root package only as a local
path dependency for `CommandResult`, `AutoCADBackend`, and `EzdxfBackend`.
No FastMCP standalone dependency was added to the root project.

## Direct dependencies

| Dependency | Constraint |
| --- | --- |
| `fastmcp` | `==3.4.4` |
| `autocad-mcp` | local path `../..` |
| `httpx` | `>=0.27,<1.0` |
| `PyJWT[crypto]` | `>=2.11.0,<3.0` |
| `asgi-lifespan` | `>=2.1,<3.0` |
| test group | `pytest`, `pytest-asyncio` |

## Local resolved versions

Command: `uv tree --locked` from `poc/fastmcp-phase0/`.

- FastMCP `3.4.4`
- MCP SDK `1.28.1`
- Starlette `1.3.1`
- Pydantic `2.13.4`
- HTTPX `0.28.1`
- PyJWT `2.13.0`
- Authlib `1.7.2`
- Ezdxf `1.4.4`
- Pillow `12.3.0`
- Python runtime checked locally: `3.13.13`

The root lockfile remains separate and was checked at MCP `1.26.0`,
Starlette `0.52.1`, and Pydantic `2.12.5`.

## Lock and source hashes

Captured before POC edits for the root project:

- root `pyproject.toml`: `E22346C7B41544BB88C14A6B66E98E460EAA51C81F7ABF61AF9B04A59E76034E`
- root `uv.lock`: `13E3871B41B59187881E5ABE27262BCE333AAB8CBD097EEADBB63622394A9974`

Current POC files:

- POC `pyproject.toml`: `6DE3C5F3EAE4CE0E49845C9F2ADE64F02BEC71632351F63B0F1A55245796AE6D`
- POC `uv.lock`: `E37AC2B30F2987748C7B2E5975121B51605CE53E20C1DB98D1897CA49789A2A8`

The POC lock was generated with `uv lock` and verified with
`uv sync --locked --group test`.
