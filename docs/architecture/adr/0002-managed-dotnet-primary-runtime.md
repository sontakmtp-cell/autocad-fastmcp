# ADR 0002: Managed .NET primary runtime with LT compatibility

- Status: Accepted for Phase 5
- Date: 2026-07-24
- Scope: Desktop Agent runtime boundary and local AutoCAD execution

## Context

The Phase 4 C1 Desktop Agent reaches AutoCAD through the existing
SafeFileIPC/AutoLISP path. That path remains required for AutoCAD LT 2024+ and
as a controlled compatibility fallback on AutoCAD Full. It must not become the
capability ceiling for Full AutoCAD or vertical products.

The Gateway must remain independent of Autodesk assemblies, COM, AutoLISP file
paths, and local process details. The Desktop Agent already owns device
identity, network transport, policy, ledger, UI, and diagnostics, so it is the
correct location for runtime selection.

## Decision

1. AutoCAD Full 2018+ uses `managed_dotnet` as the target primary runtime when
   its release-family Host package passes authenticated `cad.host/1` handshake,
   schema, package, product, document, and capability checks.
2. AutoCAD LT 2024+ keeps `autolisp_file_ipc` as its primary runtime. Full
   AutoCAD may use it only as an explicit, degraded compatibility fallback for
   read operations during migration.
3. `ezdxf_headless` remains an offline DXF runtime and never represents a live
   AutoCAD session or authorizes a DWG commit.
4. Desktop Agent owns `RuntimeBroker`. Gateway sees only additive runtime,
   product, package, capability, and execution evidence.
5. Shared capability manifests use `cad.capability/1`, bounded canonical JSON,
   and SHA-256. Unknown optional fields are retained so compatible readers do
   not silently compute a different hash.
6. Desktop Agent and Managed Host communicate locally over an authenticated
   current-user Windows Named Pipe using bounded, language-neutral
   `cad.host/1` envelopes.
7. Managed Host uses an explicit operation registry. Remote executable,
   reflection dispatch, arbitrary assembly/path/network access, raw AutoLISP,
   and arbitrary code are outside the contract.
8. Read-only fallback must report the requested and actual runtime plus a
   degradation reason. Preview, commit, validate, and rollback never silently
   change runtime.

## Migration controls

The default remains `AUTOCAD_MCP_RUNTIME_MODE=autolisp_compat` until the
Mechanical 2025 read-only POC exit is proven. Managed Host, Full compatibility
fallback, and LT runtime each have separate flags. Existing public MCP tools,
Gateway job semantics, SafeFileIPC controls, packaged dispatcher, and no-write-
retry behavior remain unchanged.

## Consequences

- Phase 5 can add Full AutoCAD capability without removing LT support.
- A new Host requires release-family packaging and real AutoCAD evidence; unit
  tests alone cannot certify it.
- Runtime/package/capability changes invalidate any future preview or consent.
- More components must be installed and diagnosed, so Agent UI exposes plain
  runtime/degradation labels while detailed identifiers stay in Diagnostics.

## Phase 5 acceptance

- Old C1 messages parse with no runtime manifest.
- New messages produce stable manifest hashes and keep optional future evidence.
- File IPC remains the first broker adapter and the migration default.
- `cad.host/1` Phase 5.1 is read-only and contains no arbitrary-code surface.
- Managed read-only E2E must be green before entity or write work is opened.
