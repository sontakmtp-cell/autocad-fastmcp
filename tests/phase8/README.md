# Phase 8 conformance foundation

This directory is an independent, production-code-free conformance baseline.
It freezes the Phase 7 public MCP surface, records adversarial compiler inputs,
defines cross-runtime claim categories, and scaffolds the drop/recovery matrix.
It also maps security control S8-010 to an exact Phase 7/Phase 8 contract-freeze
surface and recursive sensitive-field/primitive denylist.

The source/compiler vectors are data, not proof that `cad.program/1.0` exists.
They become executable contract vectors when the Contract/Compiler owner
provides a stable adapter. Until then the matrix status is
`blocked_pending_integration`; the foundation tests only validate that the
required categories and fail-closed expectations are complete.

Run:

```powershell
python scripts\test-phase8-conformance.py
.\scripts\test-phase8-regression.ps1 -ListOnly
```

The first command is the canonical single-command check. It validates the
catalogs, then invokes pytest through `uv --project services/gateway`; FastMCP
stays isolated from the root project.

The regression runner is intentionally opt-in per suite. It never changes
feature flags, launches AutoCAD, or treats ezdxf as live DWG evidence.
